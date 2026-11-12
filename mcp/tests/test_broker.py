"""Real FastMCP/broker contracts; all launched children are synthetic Python fixtures."""
import asyncio
from dataclasses import replace
import fcntl
import inspect
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time

import pytest
from fastmcp import Client

from src import broker, runtime
from src.config import Config
from src.errors import ACTIONS, WorkbenchError
from src.main import build_server
from src.models import ProcessIdentity, State, TERMINAL
from src.policy import DENIED_TOOLS, Safety, ToolSpec
from src.registry import Registry
from src.registry.process import identity, same_process
from src.tools import status as status_tools


MCP_ROOT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).parent / "fakes"
ERROR_FIELDS = {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}
LIVE_READS = {"session_state", "session_wait", "session_options_get", "session_saves_list"}


async def observed(predicate, *, seconds=20, label="expected observed state"):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"deadline waiting for {label}")
        await asyncio.sleep(0.02)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def records(path):
    try:
        lines = path.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return []
    # A concurrent append may expose its unfinished final line, not a receipt.
    return [json.loads(line) for line in lines if line.endswith("\n")]


def success(reply):
    assert "error" not in reply, reply
    assert "result" in reply, reply
    return reply["result"]


def error(reply, code=None, *, private=None):
    assert "result" not in reply, reply
    envelope = reply["error"]
    assert set(envelope) == ERROR_FIELDS
    assert envelope["code"] in ACTIONS
    assert envelope["operator_action"] == ACTIONS[envelope["code"]]
    assert isinstance(envelope["retryable"], bool)
    if code is not None:
        assert envelope["code"] == code
    raw = json.dumps(envelope)
    assert len(raw.encode()) < 2048
    assert "Traceback" not in raw and "FileNotFoundError" not in raw
    if private is not None:
        assert str(private) not in raw
    return envelope


def send_owned(birth, sig):
    """Test cleanup/signals also require a live birth identity, never a saved PID alone."""
    if birth is None or birth.pid == os.getpid() or not same_process(birth):
        return
    try:
        fd = os.pidfd_open(birth.pid)
    except ProcessLookupError:
        return
    try:
        if same_process(birth):
            signal.pidfd_send_signal(fd, sig)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


class Sandbox:
    def __init__(self, root):
        self.root = root
        self.children = []
        self.births = {}
        for name in ("state", "build", "harness", "clones", "seed", "protected", "home"):
            (root / name).mkdir(mode=0o700)
        (root / "build/romfs").mkdir()
        (root / "build/image.bin").write_bytes(b"synthetic broker test image")
        (root / "build/hookshot").write_text(f"#!{sys.executable}\nraise SystemExit(99)\n")
        (root / "build/hookshot").chmod(0o700)
        (root / "seed/save").mkdir()
        (root / "seed/save/account.sav").write_bytes(b"synthetic account")
        for name, mode in (("gate", "gate"), ("harness/hs-run.sh", "wrapper")):
            script = root / name
            script.write_text(f"#!{sys.executable}\nimport os, sys\nos.execv({sys.executable!r}, "
                f"[{sys.executable!r}, '-I', '-B', {str(FAKES / 'broker_session.py')!r}, {mode!r}, {str(root)!r}, *sys.argv[1:]])\n")
            script.chmod(0o700)
        rule = {"lanes": ["gpu"], "classes": ["ordinary"]}
        (root / "state/policy.json").write_text(json.dumps({"day": rule, "night": rule}))
        (root / "catalogue.json").write_text(json.dumps({
            "builds": {"synthetic": {"path": str(root / "build"), "reviewed": True, "owned": True}},
            "saves": {"seed": {"path": str(root / "seed")}},
            "profiles": {"ordinary": {"debug": True}}, "clone_root": str(root / "clones")}))
        self.env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ") if key in os.environ}
        self.env.update(HOME=str(root / "home"), XDG_STATE_HOME=str(root / "xdg-state"),
            XDG_CACHE_HOME=str(root / "cache"), XDG_CONFIG_HOME=str(root / "home-config"),
            PYTHONDONTWRITEBYTECODE="1", HOOKSHOT_WB_STATE_ROOT=str(root / "state"),
            HOOKSHOT_WB_READ_ROOTS=json.dumps([str(root)]), HOOKSHOT_WB_WRITE_ROOTS=json.dumps([str(root)]),
            HOOKSHOT_WB_PROTECTED_ROOTS=json.dumps([str(root / "protected")]),
            HOOKSHOT_WB_HARNESS_DIR=str(root / "harness"), HOOKSHOT_WB_GPU_GATE=str(root / "gate"),
            HOOKSHOT_WB_DEPENDENCY_PYTHON=sys.executable, HOOKSHOT_WB_SESSION_CATALOGUE=str(root / "catalogue.json"),
            HOOKSHOT_WB_PROFILE="operate", HOOKSHOT_WB_BROKER_IDLE_SECONDS="60")
        self.config = Config.from_env(self.env)
        self.registry = Registry(self.config.state_root)
        self.registry.open()

    @property
    def paths(self):
        return broker.paths(self.config)

    def remember(self, birth):
        if birth is not None and birth.pid != os.getpid():
            self.births[(birth.pid, birth.start_ticks, birth.boot_id)] = birth

    def owner(self):
        value = read_json(self.paths.owner)
        if value is None:
            return None
        birth = ProcessIdentity.model_validate(value["identity"])
        # Only the isolated fixture directory is allowed to identify our daemon.
        if same_process(birth):
            try:
                if Path(f"/proc/{birth.pid}/cwd").resolve() == self.paths.directory:
                    self.remember(birth)
            except OSError:
                pass
        return birth

    async def spawn(self, *args, capture=False):
        if capture:
            child = await asyncio.create_subprocess_exec(sys.executable, "-B", *args, cwd=MCP_ROOT,
                env=self.env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        else:
            number = len(self.children)
            with (self.root / f"child-{number}.stdout").open("wb") as stdout, (self.root / f"child-{number}.stderr").open("wb") as stderr:
                child = await asyncio.create_subprocess_exec(sys.executable, "-B", *args, cwd=MCP_ROOT,
                    env=self.env, stdin=asyncio.subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        self.children.append(child)
        self.remember(identity(child.pid))
        return child

    async def host(self, mode="serve", *args):
        child = await self.spawn("-I", str(FAKES / "broker_host.py"), mode, str(self.root), *args)
        if mode == "serve":
            def ready():
                assert child.returncode is None, "synthetic broker host exited before readiness"
                birth = self.owner()
                return birth is not None and birth.pid == child.pid and self.paths.socket.exists()
        else:
            def ready():
                assert child.returncode is None, "synthetic peer exited before readiness"
                return any(row["pid"] == child.pid and row["event"] == "ready" for row in records(self.root / "host-events.jsonl"))
        await observed(ready, label=f"{mode} host readiness")
        return child

    async def cli_raw(self, *args, seconds=20):
        child = await self.spawn("-m", "src.cli", *args, capture=True)
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=seconds)
        self.owner()
        return child.returncode, stdout.decode(), stderr.decode()

    async def cli_call(self, tool, arguments, *, profile="operate"):
        code, stdout, stderr = await self.cli_raw("call", tool, "--args", json.dumps(arguments),
            "--profile", profile, "--timeout", "15")
        assert code == 0, stderr
        assert not stderr or stderr.strip() == "hookshot-wb: broker started"
        return json.loads(stdout)

    async def call(self, tool, arguments=None, **kwargs):
        reply = await broker.request(self.config, "call", tool=tool, arguments=arguments or {}, **kwargs)
        self.owner()
        return reply

    async def launch(self, *, cli=False):
        args = {"build_id": "synthetic", "profile_id": "ordinary", "wall_seconds": 90,
            "idempotency_key": "broker-session", "capture": "none"}
        # The client deadline is much shorter than the session lifetime. Returning
        # its eventual receipt instead of the launch Job is a protocol failure.
        if cli:
            job = await self.cli_call("session_launch", args)
        else:
            job = success(await self.call("session_launch", args, auto_start=True, timeout=15))
        assert job["state"] not in TERMINAL
        assert len(job["run_ids"]) == 1
        run_id = job["run_ids"][0]
        await observed(lambda: self.registry.get_run(run_id).state == State.RUNNING, label="live synthetic session")
        await observed(lambda: any(row["event"] == "started" and row["role"] == "leaf"
            for row in records(self.root / "session-events.jsonl")), label="synthetic leaf startup")
        self.capture_births()
        return job, run_id

    def capture_births(self):
        self.owner()
        for filename in ("session-events.jsonl", "host-events.jsonl"):
            for row in records(self.root / filename):
                if "identity" in row:
                    self.remember(ProcessIdentity.model_validate(row["identity"]))
        cursor = 0
        while True:
            page = self.registry.list_jobs(cursor=cursor, limit=100)
            for job in page.jobs:
                for birth in self.registry.process(job.job_id):
                    self.remember(birth)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

    async def cleanup(self):
        self.capture_births()
        try:
            if self.paths.socket.exists():
                try:
                    await broker.request(self.config, "stop", timeout=5)
                except Exception:
                    # A deliberately broken peer is covered below by exact-identity cleanup.
                    pass
            for child in self.children:
                if child.returncode is None:
                    send_owned(next((birth for birth in self.births.values() if birth.pid == child.pid), None), signal.SIGTERM)
            deadline = time.monotonic() + 10
            while any(same_process(birth) for birth in self.births.values()) and time.monotonic() < deadline:
                self.capture_births()
                await asyncio.sleep(0.02)
            # Only test-owned identities are eligible, including retained guardian
            # identities. Leaves are killed before their gate on failure cleanup.
            rows = records(self.root / "session-events.jsonl")
            for role in ("leaf", "game", "wrapper", "gate"):
                for row in rows:
                    if row["event"] == "started" and row["role"] == role:
                        send_owned(ProcessIdentity.model_validate(row["identity"]), signal.SIGKILL)
            for birth in self.births.values():
                send_owned(birth, signal.SIGKILL)
            await asyncio.gather(*(child.wait() for child in self.children))
            await observed(lambda: all(not same_process(birth) for birth in self.births.values()),
                seconds=10, label="all owned fixture children reaped")
        finally:
            self.registry.close()


@pytest.fixture
async def sandbox():
    # The session bridge itself has a Linux sockaddr_un pathname bound. A separate
    # test below exercises long broker state paths without launching a session.
    with tempfile.TemporaryDirectory(prefix="wbb-") as directory:
        box = Sandbox(Path(directory))
        try:
            yield box
        finally:
            await box.cleanup()


def wire_request(box, action="status", **fields):
    return {"protocol": 1, "action": action, "profile": "operate", "config_hash": broker.config_hash(box.config), **fields}


async def exchange(box, payload):
    birth = box.owner()
    assert birth is not None and same_process(birth)
    reader, writer = await asyncio.open_unix_connection(f"/proc/{birth.pid}/cwd/control.sock", limit=broker.MAX_RESPONSE_BYTES + 1)
    try:
        writer.write(payload)
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=5)
        assert raw.endswith(b"\n"), raw
        assert len(raw) <= broker.MAX_RESPONSE_BYTES
        reply = json.loads(raw)
        assert set(reply) in ({"result"}, {"error"})
        return reply
    finally:
        writer.close()
        await writer.wait_closed()


async def stop_and_observe(box, expected_birth=None):
    birth = expected_birth or box.owner()
    reply = await broker.request(box.config, "stop", timeout=35)
    success(reply)
    assert reply["started"] is False
    if birth is not None:
        await observed(lambda: not same_process(birth), seconds=35, label="broker exit")
    status = success(await broker.request(box.config, "status"))
    assert status["running"] is False
    return status


def assert_ordered_session_exit(box):
    rows = records(box.root / "session-events.jsonl")
    events = [row["event"] for row in rows]
    for earlier, later in (("leaf_reaped", "game_exiting"), ("game_exiting", "game_reaped"),
                           ("game_reaped", "wrapper_receipt"), ("wrapper_receipt", "gate_released")):
        assert events.count(earlier) == 1 and events.count(later) == 1, rows
        assert events.index(earlier) < events.index(later), rows
    release = next(row for row in rows if row["event"] == "gate_released")
    assert release["descendants_exited"] is True
    for row in rows:
        if row["event"] == "started":
            assert not same_process(ProcessIdentity.model_validate(row["identity"]))


async def test_read_one_shot_uses_real_registration_without_daemon(sandbox):
    config = replace(sandbox.config, profile="observe")
    catalogue = success(await runtime.one_shot(config, "list"))
    async with Client(build_server(config)) as client:
        actual = {tool.name: tool for tool in await client.list_tools()}
    assert {row["name"] for row in catalogue} == set(actual)
    for row in catalogue:
        assert row["safety"] == "READ"
        assert row["input_schema"] == actual[row["name"]].input_schema
        assert row["requires_broker"] is (row["name"] in LIVE_READS)
        assert row["shell_route"] and "\n" not in row["shell_route"]
        assert runtime.tool_spec(config, row["name"]).requires_broker is row["requires_broker"]
    for name, arguments in (("workbench_status", {}), ("queue_status", {}), ("jobs_list", {"limit": 1})):
        success(await runtime.one_shot(config, "call", name=name, arguments=arguments))
    success(await runtime.one_shot(config, "read", name="workbench://policy"))
    for name in LIVE_READS:
        error(await runtime.one_shot(config, "call", name=name, arguments={}), private=sandbox.root)
    assert not sandbox.paths.directory.exists()
    assert not records(sandbox.root / "session-events.jsonl")


async def test_status_stop_without_broker_create_nothing(sandbox):
    for action in ("status", "stop", "status", "stop"):
        reply = await broker.request(sandbox.config, action)
        assert reply["started"] is False
        assert success(reply)["running"] is False
        assert not sandbox.paths.directory.exists()


async def test_private_endpoint_and_real_peer_credentials(sandbox):
    child = await sandbox.host()
    reply = await broker.request(sandbox.config, "status")
    current = success(reply)
    assert current["running"] is True and current["identity"]["pid"] == child.pid
    assert reply["started"] is False
    for path, mode in ((sandbox.paths.directory, 0o700), (sandbox.paths.socket, 0o600),
                       (sandbox.paths.owner, 0o600), (sandbox.paths.lock, 0o600)):
        assert stat.S_IMODE(path.stat().st_mode) == mode
    assert stat.S_ISSOCK(sandbox.paths.socket.stat().st_mode)
    reader, writer = await asyncio.open_unix_connection(f"/proc/{child.pid}/cwd/control.sock")
    try:
        pid, uid, gid = broker.peer_credentials(writer)
        assert (pid, uid, gid) == (child.pid, os.getuid(), os.getgid())
    finally:
        writer.close()
        await writer.wait_closed()
    await stop_and_observe(sandbox)


async def test_server_rejects_peer_uid_before_dispatch(sandbox):
    await sandbox.host("serve", "--reject-peer")
    requests = (wire_request(sandbox), wire_request(sandbox, "call", tool="save_clone",
        arguments={"save_id": "seed", "idempotency_key": "must-not-dispatch"}))
    for request in requests:
        reply = await exchange(sandbox, json.dumps(request).encode() + b"\n")
        error(reply, "refused_by_policy", private=sandbox.root)
    checks = [row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "credential_checked"]
    assert len(checks) == 2 and all(row["real_uid"] == os.getuid() for row in checks)
    assert sandbox.owner() is not None and same_process(sandbox.owner())
    assert sandbox.registry.list_jobs().jobs == []


async def test_concurrent_autostarts_share_one_locked_broker(sandbox):
    replies = await asyncio.gather(*(sandbox.call("jobs_list", {"limit": 1}, auto_start=True, timeout=20) for _ in range(8)))
    for reply in replies:
        assert success(reply)["jobs"] == []
    assert sum(reply["started"] for reply in replies) == 1
    birth = sandbox.owner()
    assert birth is not None and same_process(birth)
    status = success(await broker.request(sandbox.config, "status"))
    assert status["identity"] == birth.model_dump()
    fd = os.open(sandbox.paths.lock, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    repeated = await sandbox.call("jobs_list", auto_start=True)
    assert repeated["started"] is False and sandbox.owner() == birth
    await stop_and_observe(sandbox, birth)
    again = await broker.request(sandbox.config, "stop")
    assert again["started"] is False and success(again)["running"] is False


async def test_broker_supports_long_state_root_without_short_path_alias(sandbox):
    sandbox.registry.close()
    root = sandbox.root / ("long-state-" + "a" * 100) / ("nested-" + "b" * 100)
    sandbox.config = replace(sandbox.config, state_root=root)
    sandbox.env["HOOKSHOT_WB_STATE_ROOT"] = str(root)
    sandbox.registry = Registry(root)
    sandbox.registry.open()
    assert len(os.fsencode(sandbox.paths.socket)) >= 108
    success(await sandbox.call("jobs_list", auto_start=True, timeout=20))
    assert sandbox.paths.socket.is_socket()
    birth = sandbox.owner()
    assert Path(f"/proc/{birth.pid}/cwd").resolve() == sandbox.paths.directory
    assert success(await broker.request(sandbox.config, "status"))["running"] is True
    await stop_and_observe(sandbox)


async def test_live_session_and_owned_jobs_survive_separate_cli_processes(sandbox):
    job, run_id = await sandbox.launch(cli=True)
    birth = sandbox.owner()
    state = await sandbox.cli_call("session_state", {"run_id": run_id})
    assert state["controllable"] is True
    view = await sandbox.cli_call("job_read", {"job_id": job["job_id"]})
    assert view["job"]["job_id"] == job["job_id"] and view["job"]["state"] not in TERMINAL
    assert view["receipt"] is None
    assert sandbox.owner() == birth
    args = {"run_id": run_id, "steps": [{"kind": "press", "buttons": ["A"]}],
        "units": "frames", "idempotency_key": "separate-client-input"}
    submitted = await sandbox.cli_call("session_input", args)
    await observed(lambda: sandbox.registry.get_job(submitted["job_id"]).state in TERMINAL, label="owned input job receipt")
    later = await sandbox.cli_call("job_read", {"job_id": submitted["job_id"]})
    assert later["job"]["state"] == "completed" and later["receipt"]["process_exited"] is True
    assert (await sandbox.cli_call("session_input", args))["job_id"] == submitted["job_id"]
    assert sandbox.registry.get_run(run_id).state == State.RUNNING
    assert len([row for row in records(sandbox.root / "session-events.jsonl") if row["event"] == "started" and row["role"] == "game"]) == 1
    code, stdout, stderr = await sandbox.cli_raw("broker", "stop")
    assert code == 0, stderr
    assert json.loads(stdout)["running"] is False
    await observed(lambda: not same_process(birth), seconds=35, label="CLI-stopped broker")
    assert sandbox.registry.get_run(run_id).stop_reason == "broker_stop"
    assert_ordered_session_exit(sandbox)


async def test_idle_deadline_does_not_end_live_owned_session(sandbox):
    child = await sandbox.host("serve", "--idle", "1")
    _, run_id = await sandbox.launch()
    birth = sandbox.owner()
    # No broker traffic extends its idle clock during this observation interval.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        assert child.returncode is None and same_process(birth)
        assert sandbox.registry.get_run(run_id).state == State.RUNNING
        await asyncio.sleep(0.02)
    state = success(await sandbox.call("session_state", {"run_id": run_id}))
    assert state["controllable"] is True
    await stop_and_observe(sandbox)
    assert_ordered_session_exit(sandbox)


async def test_idle_broker_without_owned_work_exits(sandbox):
    child = await sandbox.host("serve", "--idle", "0.3")
    birth = sandbox.owner()
    await observed(lambda: not same_process(birth), seconds=10, label="unused broker idle exit")
    assert await child.wait() == 0
    reply = await broker.request(sandbox.config, "status")
    assert reply["started"] is False and success(reply)["running"] is False


@pytest.mark.parametrize("cause,reason", [("stop", "broker_stop"), ("signal", "broker_signal"), ("deadline", "broker_deadline")])
async def test_shutdown_records_reason_after_child_before_gate_teardown(sandbox, cause, reason):
    args = ("--lifetime", "15") if cause == "deadline" else ()
    child = await sandbox.host("serve", *args)
    job, run_id = await sandbox.launch()
    birth = sandbox.owner()
    if cause == "stop":
        success(await broker.request(sandbox.config, "stop", timeout=35))
    elif cause == "signal":
        send_owned(birth, signal.SIGTERM)
    await observed(lambda: not same_process(birth), seconds=40, label=f"broker {cause} teardown")
    assert await child.wait() == 0
    run = sandbox.registry.get_run(run_id)
    assert run.state in TERMINAL and run.ended_at is not None
    assert run.stop_reason == reason
    assert sandbox.registry.get_job(job["job_id"]).state in TERMINAL
    assert_ordered_session_exit(sandbox)
    process_jobs = [row for row in sandbox.registry.list_jobs(limit=100).jobs
        if sandbox.registry.latest_event(row.job_id, "process_started") is not None]
    assert len(process_jobs) == 1
    receipt = sandbox.registry.receipt(process_jobs[0].job_id)
    assert receipt is not None and receipt.process_exited and receipt.descendants_exited


async def test_crashed_broker_recovery_reports_orphan_without_adoption(sandbox):
    first = await sandbox.host()
    job, run_id = await sandbox.launch()
    old = sandbox.owner()
    send_owned(old, signal.SIGKILL)
    assert await asyncio.wait_for(first.wait(), timeout=5) == -signal.SIGKILL
    await observed(lambda: any(row["event"] == "gate_released" for row in records(sandbox.root / "session-events.jsonl")),
        seconds=35, label="parent-death guardian session cleanup")
    await observed(lambda: all(not same_process(ProcessIdentity.model_validate(row["identity"]))
        for row in records(sandbox.root / "session-events.jsonl") if row["event"] == "started"),
        seconds=35, label="actual exits after the gate completion marker")
    assert_ordered_session_exit(sandbox)
    before = sandbox.registry.get_run(run_id)
    second = await sandbox.host()
    new = sandbox.owner()
    assert new.pid == second.pid and new != old
    status = success(await broker.request(sandbox.config, "status"))
    assert run_id not in status["owned_run_ids"]
    orphan = next(row for row in status["orphaned_runs"] if row["run_id"] == run_id)
    assert orphan["reason"] == "owner_not_present"
    error(await sandbox.call("session_state", {"run_id": run_id}), "refused_by_policy")
    error(await sandbox.call("session_stop", {"run_id": run_id, "reason": "must not adopt", "idempotency_key": "orphan-stop"}), "refused_by_policy")
    await stop_and_observe(sandbox)
    assert sandbox.registry.get_run(run_id) == before
    assert sandbox.registry.get_job(job["job_id"]).job_id == job["job_id"]
    assert len([row for row in records(sandbox.root / "session-events.jsonl") if row["event"] == "started" and row["role"] == "game"]) == 1


@pytest.mark.parametrize("mismatch", ["start_ticks", "boot_id"])
async def test_stale_owner_identity_never_signals_reused_pid(sandbox, mismatch):
    sentinel = await sandbox.host("sentinel")
    birth = identity(sentinel.pid)
    forged = birth.model_copy(update={mismatch: birth.start_ticks + 1 if mismatch == "start_ticks" else "different-boot"})
    sandbox.paths.directory.mkdir(mode=0o700)
    sandbox.paths.owner.write_text(json.dumps({"protocol": 1, "identity": forged.model_dump(), "config_hash": broker.config_hash(sandbox.config)}))
    sandbox.paths.owner.chmod(0o600)
    for action in ("status", "stop"):
        reply = await broker.request(sandbox.config, action)
        status = success(reply)
        assert status["running"] is False and status["stale"] is True
        assert reply["started"] is False
        assert same_process(birth)
    success(await sandbox.call("jobs_list", auto_start=True, timeout=20))
    assert sandbox.owner().pid != sentinel.pid
    await stop_and_observe(sandbox)
    assert same_process(birth)
    assert not [row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "signal"]


async def test_orphan_registry_pids_are_observed_not_adopted_or_killed(sandbox):
    sentinel = await sandbox.host("sentinel")
    live = identity(sentinel.pid)
    reused = live.model_copy(update={"start_ticks": live.start_ticks + 1})
    job = sandbox.registry.create_job("session_launch")
    sandbox.registry.bind_operation(job.job_id, reused)
    run = sandbox.registry.create_run(state=State.RUNNING)
    sandbox.registry.own_run(job.job_id, run.run_id)
    await sandbox.host()
    status = success(await broker.request(sandbox.config, "status"))
    assert run.run_id not in status["owned_run_ids"]
    assert any(row["run_id"] == run.run_id and row["reason"] == "owner_not_present" for row in status["orphaned_runs"])
    error(await sandbox.call("session_stop", {"run_id": run.run_id, "reason": "not ours", "idempotency_key": "stored-pid"}), "refused_by_policy")
    await stop_and_observe(sandbox)
    assert same_process(live)
    assert sandbox.registry.get_run(run.run_id) == run
    assert not sandbox.registry.cancellation_requested(job.job_id)
    assert not [row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "signal"]


@pytest.mark.parametrize("stale_owner", [False, True])
async def test_live_foreign_socket_is_not_deleted_or_replaced(sandbox, stale_owner):
    foreign = await sandbox.host("foreign")
    birth = identity(foreign.pid)
    inode = sandbox.paths.socket.stat().st_ino
    if stale_owner:
        wrong = birth.model_copy(update={"start_ticks": birth.start_ticks + 1})
        sandbox.paths.owner.write_text(json.dumps({"protocol": 1, "identity": wrong.model_dump(), "config_hash": broker.config_hash(sandbox.config)}))
        sandbox.paths.owner.chmod(0o600)
    reply = await sandbox.call("jobs_list", auto_start=True, timeout=5)
    error(reply, private=sandbox.root)
    assert reply["started"] is False
    assert sandbox.paths.socket.stat().st_ino == inode and same_process(birth)
    await broker.request(sandbox.config, "stop", timeout=5)
    assert sandbox.paths.socket.stat().st_ino == inode and same_process(birth)
    assert not [row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "signal"]


async def test_dead_socket_and_dead_owner_are_replaced_safely(sandbox):
    peer = await sandbox.host("foreign")
    birth = identity(peer.pid)
    sandbox.paths.owner.write_text(json.dumps({"protocol": 1, "identity": birth.model_dump(), "config_hash": broker.config_hash(sandbox.config)}))
    sandbox.paths.owner.chmod(0o600)
    send_owned(birth, signal.SIGTERM)
    await asyncio.wait_for(peer.wait(), timeout=5)
    assert sandbox.paths.socket.exists() and not same_process(birth)
    success(await sandbox.call("jobs_list", auto_start=True, timeout=20))
    assert sandbox.owner() != birth
    await stop_and_observe(sandbox)


async def test_profile_selection_is_exact_and_never_escalates(sandbox):
    config = replace(sandbox.config, profile="observe")
    with pytest.raises(WorkbenchError) as refused:
        runtime.tool_spec(config, "session_launch")
    assert refused.value.envelope.code == "not_found"
    error(await broker.request(config, "call", tool="session_launch", arguments={}, auto_start=True), "not_found")
    assert not sandbox.paths.directory.exists()
    assert runtime.tool_spec(replace(config, extra_tools=("session_input",)), "session_input").requires_broker is True
    for profile in ("Operate", "operate ", "unknown", ""):
        error(await broker.request(sandbox.config, "call", tool="jobs_list", profile=profile, auto_start=True), "invalid_argument")
        assert not sandbox.paths.directory.exists()
    await sandbox.host()
    error(await broker.request(sandbox.config, "call", tool="session_launch", arguments={}, profile="observe"), "not_found")
    success(await broker.request(sandbox.config, "call", tool="jobs_list", profile="observe"))
    assert sandbox.registry.list_jobs().jobs == []


@pytest.mark.parametrize("safety", [Safety.PUBLIC, Safety.DANGER])
async def test_public_and_danger_specs_cannot_enter_real_registration(sandbox, monkeypatch, safety):
    monkeypatch.setattr(status_tools, "SPECS", (*status_tools.SPECS, ToolSpec("fixture_forbidden", safety, "must not register")))
    error(await runtime.one_shot(sandbox.config, "list"), private=sandbox.root)
    with pytest.raises(WorkbenchError):
        runtime.tool_spec(sandbox.config, "fixture_forbidden")
    error(await sandbox.call("fixture_forbidden", auto_start=True), private=sandbox.root)
    assert not sandbox.paths.directory.exists()


async def test_no_raw_command_or_denied_tool_endpoint(sandbox):
    catalogue = success(await runtime.one_shot(sandbox.config, "list"))
    assert not ({row["name"] for row in catalogue} & DENIED_TOOLS)
    assert {row["safety"] for row in catalogue} <= {"READ", "RUN", "WRITE_LOCAL"}
    await sandbox.host()
    for name in sorted(DENIED_TOOLS | {"unknown_tool", "Session_State", "jobs_list ", "/private/raw-command"}):
        error(await sandbox.call(name, {"argv": ["touch", str(sandbox.root / "must-not-exist")]}), private=sandbox.root)
    request = wire_request(sandbox, "exec", argv=[sys.executable, "-c", "raise SystemExit(99)"])
    error(await exchange(sandbox, json.dumps(request).encode() + b"\n"), "invalid_argument")
    assert not (sandbox.root / "must-not-exist").exists()
    assert sandbox.registry.list_jobs().jobs == []


@pytest.mark.parametrize("payload", [
    b"not json\n", b"[]\n", b"null\n", b"{\"protocol\":1}\n",
    b'{"protocol":2,"action":"status","profile":"operate"}\n',
    b'{"protocol":1,"action":"status","profile":"operate","raw_command":"/private/sensitive"}\n',
    b'{"protocol":1,"action":"call","profile":"operate","tool":"jobs_list","arguments":[]}\n',
    b'{"protocol":1,"action":"status","profile":"operate","timeout":-1}\n',
])
async def test_malformed_wire_requests_return_bounded_fixed_errors(sandbox, payload):
    await sandbox.host()
    error(await exchange(sandbox, payload), "invalid_argument", private="/private/sensitive")
    assert success(await broker.request(sandbox.config, "status"))["running"] is True


async def test_oversized_request_and_invalid_tool_arguments_are_bounded(sandbox):
    await sandbox.host()
    payload = json.dumps(wire_request(sandbox, "call", tool="jobs_list", arguments={"private": "x" * broker.MAX_REQUEST_BYTES})).encode() + b"\n"
    error(await exchange(sandbox, payload), "invalid_argument")
    for arguments in ({"limit": 101}, {"cursor": -1}, {"unregistered": str(sandbox.root)}):
        error(await sandbox.call("jobs_list", arguments), "invalid_argument", private=sandbox.root)
    assert success(await broker.request(sandbox.config, "status"))["running"] is True


async def test_registry_and_orphan_status_pages_remain_bounded(sandbox):
    dead = ProcessIdentity(pid=2147483647, start_ticks=1, boot_id="synthetic-dead-owner", process_group=2147483647, session_id=2147483647)
    runs = set()
    for _ in range(105):
        job = sandbox.registry.create_job("session_launch")
        sandbox.registry.bind_operation(job.job_id, dead)
        run = sandbox.registry.create_run(state=State.RUNNING)
        sandbox.registry.own_run(job.job_id, run.run_id)
        runs.add(run.run_id)
    await sandbox.host()
    page = success(await sandbox.call("jobs_list", {"limit": 100}))
    assert len(page["jobs"]) == 100 and page["next_cursor"] is not None
    next_page = success(await sandbox.call("jobs_list", {"limit": 100, "cursor": page["next_cursor"]}))
    assert len(next_page["jobs"]) == 5 and next_page["next_cursor"] is None
    assert not ({row["job_id"] for row in page["jobs"]} & {row["job_id"] for row in next_page["jobs"]})
    status = success(await broker.request(sandbox.config, "status"))
    assert len(status["orphaned_runs"]) == 100 and status["orphaned_next_cursor"] is not None
    assert {row["run_id"] for row in status["orphaned_runs"]} <= runs
    assert len(status["active_job_ids"]) <= 100 and len(status["owned_run_ids"]) <= 100
    assert len(json.dumps(status).encode()) <= broker.MAX_RESPONSE_BYTES


async def test_server_response_overflow_returns_fixed_backend_error(sandbox):
    await sandbox.host("serve", "--response-limit", "1024")
    error(await sandbox.call("workbench_status"), "backend_failed", private=sandbox.root)
    assert success(await sandbox.call("jobs_list", {"limit": 1}))["jobs"] == []


async def test_oversized_peer_response_is_not_returned_to_client(sandbox):
    await sandbox.host("oversized")
    reply = await sandbox.call("jobs_list", timeout=5)
    envelope = error(reply, "uncertain_outcome", private=sandbox.root)
    assert "x" * 128 not in json.dumps(envelope)
    assert reply["started"] is False


async def test_oversized_outgoing_request_never_reaches_peer(sandbox):
    await sandbox.host("drop")
    reply = await sandbox.call("jobs_list", {"private": "x" * broker.MAX_REQUEST_BYTES}, timeout=5)
    error(reply, "invalid_argument", private=sandbox.root)
    assert not [row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "request"]


async def test_dropped_mutation_reply_is_uncertain_and_never_replayed(sandbox):
    await sandbox.host("drop")
    reply = await sandbox.call("session_stop", {"run_id": "run_" + "0" * 32,
        "reason": "synthetic transport test", "idempotency_key": "one-send"}, auto_start=True, timeout=5)
    envelope = error(reply, "uncertain_outcome", private=sandbox.root)
    assert envelope["retryable"] is False and reply["started"] is False
    assert len([row for row in records(sandbox.root / "host-events.jsonl") if row["event"] == "mutation_received"]) == 1


async def test_invalid_cli_call_has_only_fixed_error_and_no_daemon(sandbox):
    private = str(sandbox.root / "private-command")
    code, stdout, stderr = await sandbox.cli_raw("call", private, "--args", json.dumps({"secret": private}))
    assert code != 0 and stdout == ""
    error({"error": json.loads(stderr)}, "not_found", private=private)
    assert not sandbox.paths.directory.exists()
