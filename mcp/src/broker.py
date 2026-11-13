"""Same-user Unix broker preserving the real MCP lifespan across CLI calls.

There is no network listener, raw-command operation or stored-PID recovery kill.
"""
import asyncio
from dataclasses import asdict, dataclass, fields, replace
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from typing import Literal, get_args

from fastmcp import Client
from pydantic import Field, ValidationError
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError, fail
from src.main import build_server
from src.models import ErrorEnvelope, ProcessIdentity, Record, utc_now
from src.models.knowledge import KnowledgeConfig
from src.policy import validate_state_root
from src.registry import Registry
from src.registry.process import identity, same_process
from src.runtime import error_reply, execute, tool_spec
from src.tools.registry import select

PROTOCOL = 1
MAX_REQUEST_BYTES = 262144
MAX_RESPONSE_BYTES = 8388608
MAX_CONFIG_BYTES = 1048576


@dataclass(frozen=True)
class BrokerPaths:
    directory: Path
    socket: Path
    lock: Path
    owner: Path


def paths(config: Config) -> BrokerPaths:
    root = config.state_root / "cli"
    return BrokerPaths(root, root / "control.sock", root / "broker.lock", root / "owner.json")


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("Unsupported configuration type")


def _config_bytes(config: Config) -> bytes:
    return json.dumps(asdict(config), default=_json_default, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def config_hash(config: Config) -> str:
    # Selecting a narrower profile does not start a second broker or lose sessions.
    return hashlib.sha256(_config_bytes(replace(config, profile="operate"))).hexdigest()


def config_from_fd(fd: int) -> Config:
    try:
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or data.keys() - {field.name for field in fields(Config)}:
            raise ValueError
        for field in fields(Config):
            value = data.get(field.name)
            if value is None:
                continue
            if field.name.endswith("_roots"):
                data[field.name] = tuple(Path(item) for item in value)
            elif field.type is Path or Path in get_args(field.type):
                data[field.name] = Path(value)
            elif field.name == "extra_tools":
                data[field.name] = tuple(value)
            elif field.name == "knowledge":
                data[field.name] = KnowledgeConfig.model_validate(value)
        return Config(**data)
    except (OSError, ValueError, TypeError):
        raise fail("invalid_argument", "broker_configuration") from None


class BrokerRequest(Record):
    protocol: Literal[1] = 1
    action: Literal["call", "status", "stop"]
    tool: str | None = Field(default=None, max_length=64)
    arguments: dict = Field(default_factory=dict)
    profile: Literal["observe", "operate"] = "observe"
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    timeout: float = Field(default=60, gt=0, le=3600)


class BrokerOwner(Record):
    identity: ProcessIdentity
    config_hash: str
    protocol: Literal[1] = 1


def peer_credentials(writer) -> tuple[int, int, int]:
    transport_socket = writer.get_extra_info("socket")
    raw = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def _private_directory(config: Config, *, create: bool) -> BrokerPaths:
    validate_state_root(config)
    p = paths(config)
    if create:
        config.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        p.directory.mkdir(mode=0o700, exist_ok=True)
    if p.directory.exists() or p.directory.is_symlink():
        info = p.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise fail("refused_by_policy", "broker_directory")
    return p


def _read_owner(p: BrokerPaths) -> BrokerOwner | None:
    try:
        fd = os.open(p.owner, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise fail("refused_by_policy", "broker_owner")
            raw = stream.read(8193)
        if len(raw) > 8192:
            return None
        return BrokerOwner.model_validate_json(raw)
    except (FileNotFoundError, ValueError):
        return None
    except OSError:
        raise fail("refused_by_policy", "broker_owner") from None


def _open_lock(p: BrokerPaths, *, create: bool) -> int | None:
    try:
        flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
        fd = os.open(p.lock, flags, 0o600)
    except FileNotFoundError:
        return None
    except OSError:
        raise fail("refused_by_policy", "broker_lock") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(fd)
        raise fail("refused_by_policy", "broker_lock")
    return fd


def _lock_held(p: BrokerPaths) -> bool:
    fd = _open_lock(p, create=False)
    if fd is None:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def _orphaned(registry: Registry, owned_run_ids=(), *, cursor=0, limit=100):
    page = registry.list_runs(active_only=True, cursor=cursor, limit=limit)
    owned = set(owned_run_ids)
    orphans = []
    for run in page.runs:
        if run.run_id in owned:
            continue
        owner = registry.run_process_owner(run.run_id)
        if run.gate.device not in {"gpu", "intel"} and owner is None:
            continue
        if owner is None or not same_process(owner):
            orphans.append({"run_id": run.run_id, "state": run.state.value, "reason": "owner_not_present"})
    return orphans, page.next_cursor


def _inactive_status(config, p):
    registry = Registry(config.state_root)
    orphans, cursor = [], None
    try:
        if registry.open_readonly():
            orphans, cursor = _orphaned(registry)
    finally:
        registry.close()
    return {"running": False, "identity": None, "observed_at": utc_now(), "owned_run_ids": [],
            "active_job_ids": [], "orphaned_runs": orphans, "orphaned_next_cursor": cursor,
            "stale": p.owner.exists() or p.socket.exists(), "stopping": False}


async def _close_writer(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
    except (OSError, TimeoutError):
        pass


async def _connect(p, owner):
    if not same_process(owner.identity) or not _lock_held(p):
        raise fail("unavailable", "broker_connect")
    # A short procfs path avoids Unix socket path-length limits without a socket
    # outside the private state directory. Both peer credentials and birth match.
    address = f"/proc/{owner.identity.pid}/cwd/{p.socket.name}"
    reader, writer = await asyncio.open_unix_connection(address, limit=MAX_RESPONSE_BYTES + 1)
    pid, uid, _ = peer_credentials(writer)
    if uid != os.getuid() or pid != owner.identity.pid or not same_process(owner.identity):
        await _close_writer(writer)
        raise fail("refused_by_policy", "broker_peer")
    return reader, writer


async def _socket_active(p):
    try:
        info = p.socket.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise fail("refused_by_policy", "broker_socket")
    directory_fd = os.open(p.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_unix_connection(
                f"/proc/self/fd/{directory_fd}/{p.socket.name}"), timeout=0.5)
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ECONNREFUSED}:
                return False
            raise fail("refused_by_policy", "broker_socket") from None
        except TimeoutError:
            raise fail("busy", "broker_socket") from None
        await _close_writer(writer)
        return True
    finally:
        os.close(directory_fd)


async def _start(config, p, deadline):
    while time.monotonic() < deadline:
        owner = _read_owner(p)
        if owner is not None and same_process(owner.identity) and _lock_held(p):
            return owner, False
        fd = _open_lock(p, create=True)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                if owner is not None and same_process(owner.identity):
                    raise fail("busy", "broker_start")
                if await _socket_active(p):
                    raise fail("busy", "broker_socket")
                raw = _config_bytes(config)
                if len(raw) > MAX_CONFIG_BYTES:
                    raise fail("invalid_argument", "broker_configuration")
                config_fd = os.memfd_create("ls-wb-config", os.MFD_CLOEXEC)
                log_fd = os.open(p.directory / "server.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                try:
                    with os.fdopen(os.dup(config_fd), "wb") as stream:
                        stream.write(raw)
                    os.lseek(config_fd, 0, os.SEEK_SET)
                    child = subprocess.Popen([sys.executable, "-I", "-m", "src.cli", "serve",
                        "--lock-fd", str(fd), "--config-fd", str(config_fd)],
                        cwd=p.directory, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
                        pass_fds=(fd, config_fd), start_new_session=True)
                finally:
                    os.close(config_fd)
                    os.close(log_fd)
                # Do not hold a second copy of the lock while observing readiness.
                os.close(fd)
                fd = None
                while time.monotonic() < deadline:
                    owner = _read_owner(p)
                    if owner is not None and owner.identity.pid == child.pid and same_process(owner.identity):
                        return owner, True
                    if child.poll() is not None:
                        raise fail("unavailable", "broker_start")
                    await asyncio.sleep(0.025)
                raise fail("timeout", "broker_start")
        finally:
            if fd is not None:
                os.close(fd)
        await asyncio.sleep(0.025)
    raise fail("timeout", "broker_start")


async def request(config: Config, action: str, *, tool=None, arguments=None, profile=None,
                  auto_start=False, timeout=60) -> dict:
    """Send at most one request. A lost mutation reply is never replayed."""
    started, sent, writer = False, False, None
    try:
        profile = config.profile if profile is None else profile
        message = BrokerRequest(action=action, tool=tool, arguments=arguments or {}, profile=profile,
                                config_hash=config_hash(config), timeout=timeout)
        if action == "call":
            tool_spec(replace(config, profile=profile), tool)
        payload = message.model_dump_json().encode() + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise fail("invalid_argument", "broker_request")
        p = _private_directory(config, create=False)
        owner = _read_owner(p) if p.directory.exists() else None
        async with asyncio.timeout(timeout):
            if owner is None or not same_process(owner.identity) or not _lock_held(p):
                if action in {"status", "stop"}:
                    result = _inactive_status(config, p)
                    if action == "stop":
                        result["stopped"] = False
                    return {"result": result, "started": False}
                if not auto_start:
                    raise fail("unavailable", "broker_connect")
                p = _private_directory(config, create=True)
                owner, started = await _start(config, p, time.monotonic() + min(timeout, 15))
            if action == "call" and owner.config_hash != message.config_hash:
                raise fail("busy", "broker_configuration")
            reader, writer = await _connect(p, owner)
            sent = True
            writer.write(payload)
            await writer.drain()
            raw = await reader.readline()
            if not raw.endswith(b"\n") or len(raw) > MAX_RESPONSE_BYTES:
                raise fail("uncertain_outcome", "broker_reply")
            reply = json.loads(raw)
            if not isinstance(reply, dict) or set(reply) not in ({"result"}, {"error"}):
                raise fail("uncertain_outcome", "broker_reply")
            if "error" in reply:
                reply["error"] = ErrorEnvelope.model_validate(reply["error"]).model_dump()
            if action == "stop" and "result" in reply:
                while _lock_held(p):
                    await asyncio.sleep(0.025)
                reply["result"].update(_inactive_status(config, p))
                reply["result"]["stopped"] = True
            return {**reply, "started": started}
    except TimeoutError:
        error = fail("uncertain_outcome" if sent else "timeout", "broker_request")
        return {**error_reply(error), "started": started}
    except (ValueError, ValidationError):
        error = fail("uncertain_outcome" if sent else "invalid_argument", "broker_request")
        return {**error_reply(error), "started": started}
    except WorkbenchError as exc:
        return {**error_reply(exc), "started": started}
    except Exception:
        error = fail("uncertain_outcome" if sent else "unavailable", "broker_request")
        return {**error_reply(error), "started": started}
    finally:
        if writer is not None:
            await _close_writer(writer)


class BrokerServer:
    def __init__(self, config: Config, *, idle_seconds=None):
        self.config = config
        self.idle_seconds = config.broker_idle_seconds if idle_seconds is None else idle_seconds
        if not 0.05 <= self.idle_seconds <= 3600:
            raise fail("invalid_argument", "broker_idle")
        self.paths = paths(config)
        self.deps = Deps(replace(config, profile="operate"), reconcile_on_open=False)
        self.server = build_server(deps=self.deps)
        self.client = None
        self.identity = None
        self.stopping = asyncio.Event()
        self.reason = "broker_idle"
        self.last_activity = time.monotonic()
        self.inflight = 0
        self.handlers = set()

    def _owned_runs(self):
        service = self.deps.services.get("session")
        return service.active_run_ids() if service is not None else []

    def _owns_work(self):
        return bool(self.deps.supervisor.tasks or self._owned_runs())

    def _status(self, arguments=None):
        args = arguments or {}
        if args.keys() - {"cursor", "limit"}:
            raise fail("invalid_argument", "broker_status")
        owned = self._owned_runs()
        orphans, cursor = _orphaned(self.deps.registry, owned, cursor=args.get("cursor", 0), limit=args.get("limit", 100))
        return {"running": True, "identity": self.identity.model_dump(), "observed_at": utc_now(),
                "owned_run_ids": owned[:100], "active_job_ids": sorted(self.deps.supervisor.tasks)[:100],
                "orphaned_runs": orphans, "orphaned_next_cursor": cursor,
                "stale": False, "stopping": self.stopping.is_set()}

    def stop(self, reason):
        if not self.stopping.is_set():
            self.reason = reason
            self.stopping.set()

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.inflight += 1
        try:
            _, uid, _ = peer_credentials(writer)
            if uid != os.getuid():
                raise fail("refused_by_policy", "broker_peer")
            async with asyncio.timeout(5):
                raw = await reader.readline()
            if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST_BYTES:
                raise fail("invalid_argument", "broker_request")
            message = BrokerRequest.model_validate_json(raw)
            self.last_activity = time.monotonic()
            if message.action == "status":
                reply = {"result": self._status(message.arguments)}
            elif message.action == "stop":
                self.stop("broker_stop")
                reply = {"result": {"stopping": True, "stopped": False}}
            else:
                if self.stopping.is_set():
                    raise fail("busy", "broker_stopping")
                if message.config_hash != config_hash(self.config):
                    raise fail("busy", "broker_configuration")
                allowed = select(self.deps.specs, message.profile, self.config.extra_tools)
                if message.tool not in allowed:
                    raise fail("not_found", "tool_call")
                if message.tool == "job_cancel":
                    target = message.arguments.get("job_id")
                    job = self.deps.registry.get_job(target)
                    if job.state.value not in {"completed", "failed", "cancelled", "interrupted"} and target not in self.deps.supervisor.tasks:
                        # This broker did not start it. Never turn a stored PID into adoption.
                        raise fail("refused_by_policy", "broker_ownership")
                async with asyncio.timeout(message.timeout):
                    reply = await execute(self.client, "call", name=message.tool, arguments=message.arguments)
        except (ValueError, ValidationError):
            reply = error_reply(fail("invalid_argument", "broker_request"))
        except TimeoutError:
            reply = error_reply(fail("uncertain_outcome", "broker_request"))
        except asyncio.CancelledError:
            reply = error_reply(fail("uncertain_outcome", "broker_request"))
        except Exception as exc:
            reply = error_reply(exc, "broker_request")
        try:
            encoded = json.dumps(reply, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            if len(encoded) > MAX_RESPONSE_BYTES:
                encoded = json.dumps(error_reply(fail("backend_failed", "broker_output_limit"))).encode() + b"\n"
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), timeout=5)
        except (OSError, ValueError, TimeoutError):
            pass
        finally:
            await _close_writer(writer)
            self.last_activity = time.monotonic()
            self.inflight -= 1
            self.handlers.discard(task)

    async def _remove_stale_socket(self):
        p = self.paths
        try:
            info = p.socket.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise fail("refused_by_policy", "broker_socket")
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(p.socket.name), timeout=0.5)
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ECONNREFUSED}:
                raise fail("refused_by_policy", "broker_socket") from None
        except TimeoutError:
            raise fail("busy", "broker_socket") from None
        else:
            await _close_writer(writer)
            raise fail("busy", "broker_socket")
        p.socket.unlink(missing_ok=True)

    async def serve(self, *, lock_fd=None, lifetime_seconds=None):
        if lifetime_seconds is not None and not 0.05 <= lifetime_seconds <= 86400:
            raise fail("invalid_argument", "broker_lifetime")
        p = _private_directory(self.config, create=True)
        fd = _open_lock(p, create=True) if lock_fd is None else lock_fd
        listener, owned_socket = None, False
        old_cwd = Path.cwd()
        handlers = {}
        try:
            if lock_fd is not None:
                actual, expected = os.fstat(fd), p.lock.stat(follow_symlinks=False)
                if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                    raise fail("refused_by_policy", "broker_lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise fail("busy", "broker_start") from None
            old_owner = _read_owner(p)
            if old_owner is not None and same_process(old_owner.identity):
                raise fail("busy", "broker_start")
            os.chdir(p.directory)
            await self._remove_stale_socket()
            self.identity = identity(os.getpid())
            if self.identity is None:
                raise fail("unavailable", "broker_identity")
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):
                handlers[signum] = signal.getsignal(signum)
                loop.add_signal_handler(signum, self.stop, "broker_signal")
            async with Client(self.server) as client:
                self.client = client
                listener = await asyncio.start_unix_server(self._handle, path=p.socket.name, limit=MAX_REQUEST_BYTES + 1)
                p.socket.chmod(0o600)
                owned_socket = True
                owner = BrokerOwner(identity=self.identity, config_hash=config_hash(self.config))
                temporary = p.owner.with_name("owner." + str(os.getpid()) + ".new")
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(out, "w") as stream:
                    stream.write(owner.model_dump_json())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, p.owner)
                began = time.monotonic()
                try:
                    while not self.stopping.is_set():
                        now = time.monotonic()
                        if lifetime_seconds is not None and now - began >= lifetime_seconds:
                            self.stop("broker_deadline")
                        elif not self.inflight and not self._owns_work() and now - self.last_activity >= self.idle_seconds:
                            self.stop("broker_idle")
                        try:
                            await asyncio.wait_for(self.stopping.wait(), timeout=min(self.idle_seconds, 0.1))
                        except TimeoutError:
                            pass
                except asyncio.CancelledError:
                    self.stop("broker_signal")
                    raise
                except Exception:
                    self.stop("broker_failure")
                    raise
                finally:
                    listener.close()
                    await listener.wait_closed()
                    self.deps.shutdown_reason = self.reason
                    pending = list(self.handlers)
                    if pending:
                        try:
                            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)
                        except TimeoutError:
                            pass
            return self.reason
        finally:
            if listener is not None:
                listener.close()
                await listener.wait_closed()
            if owned_socket:
                p.socket.unlink(missing_ok=True)
                owner = _read_owner(p)
                if owner is not None and owner.identity == self.identity:
                    p.owner.unlink(missing_ok=True)
            for signum, handler in handlers.items():
                asyncio.get_running_loop().remove_signal_handler(signum)
                signal.signal(signum, handler)
            os.chdir(old_cwd)
            os.close(fd)


async def serve(config: Config, *, idle_seconds=None, lifetime_seconds=None, lock_fd=None):
    return await BrokerServer(config, idle_seconds=idle_seconds).serve(lock_fd=lock_fd, lifetime_seconds=lifetime_seconds)
