"""Bounded file and trusted subprocess operations shared by knowledge readers."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from src.models.knowledge import KnowledgeConfig, UntrustedText

FILE_CAP = 2 * 1024 * 1024
OUTPUT_CAP = 4 * 1024 * 1024
TEXT_CAP = 4096


def config(deps) -> KnowledgeConfig:
    return getattr(deps.config, "knowledge", None) or KnowledgeConfig()


def text(value, cap=TEXT_CAP) -> UntrustedText:
    if value is None:
        return UntrustedText(text=None)
    value = str(value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return UntrustedText(text=value[:cap], truncated=len(value) > cap)


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def cursor_encode(revision, offset) -> str:
    return base64.urlsafe_b64encode(json.dumps([revision, offset], separators=(",", ":")).encode()).decode().rstrip("=")


def cursor_decode(deps, value, revision, operation) -> int:
    if value is None:
        return 0
    try:
        if len(value) > 256:
            raise ValueError
        saved, offset = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise deps.error("invalid_argument", operation) from None
    if saved != revision:
        raise deps.error("busy", operation)
    return offset


def bounded_int(deps, value, low, high, operation):
    if type(value) is not int or not low <= value <= high:
        raise deps.error("invalid_argument", operation)
    return value


def checked_path(deps, path, operation, *, directory=False) -> Path:
    if path is None:
        raise deps.error("unavailable", operation)
    result = deps.paths.read(Path(path))
    if not (result.is_dir() if directory else result.is_file()):
        raise deps.error("unavailable", operation)
    return result


def under(deps, root, relative, operation) -> Path:
    root = checked_path(deps, root, operation, directory=True)
    path = deps.paths.read(root / relative)
    if not path.is_relative_to(root):
        raise deps.error("refused_by_policy", operation)
    return path


def read_bytes(deps, path, operation, cap=FILE_CAP) -> bytes:
    path = checked_path(deps, path, operation)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(cap + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > cap:
        raise deps.error("unavailable", operation)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise deps.error("busy", operation)
    return raw


def read_json(deps, path, operation):
    try:
        return json.loads(read_bytes(deps, path, operation))
    except (ValueError, UnicodeError):
        raise deps.error("backend_failed", operation) from None


def optional_json(deps, root, relative, operation):
    # Check containment/protection even for an absent optional file.
    root = checked_path(deps, root, operation, directory=True)
    path = root / relative
    try:
        path = under(deps, root, relative, operation)
    except Exception as error:
        if getattr(getattr(error, "envelope", None), "code", None) == "not_found":
            return None
        raise
    return read_json(deps, path, operation)


def read_environment() -> dict[str, str]:
    # Only official trusted readers receive their existing sign-in location. No token
    # lookup, account settings read, credential export or caller-supplied environment.
    env = {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "GH_CONFIG_DIR", "LANG", "LC_ALL", "TZ") if key in os.environ}
    env.update(PATH=os.defpath, GH_PROMPT_DISABLED="1", GH_PAGER="cat", NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
    return env


async def command(deps, argv, operation, *, timeout=None, cwd=None, cap=OUTPUT_CAP, owned_group=False, unavailable_codes=()) -> bytes:
    """Drain bounded stdout/stderr; a cancelled short reader owns only its own group.

    Supervised workers pass owned_group=True: their guardian owns and reaps the entire
    group, including a reader's children. No shell, arbitrary command or Python text is
    accepted by an MCP argument.
    """
    timeout = timeout or config(deps).read_timeout_seconds
    try:
        process = await asyncio.create_subprocess_exec(*map(str, argv), cwd=cwd, env=read_environment(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=not owned_group)
    except OSError:
        raise deps.error("unavailable", operation) from None

    async def collect(stream):
        result = bytearray()
        while block := await stream.read(65536):
            if len(result) + len(block) > cap:
                raise deps.error("backend_failed", operation)
            result.extend(block)
        return bytes(result)

    async def terminate():
        if not owned_group:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 0.5)
        except TimeoutError:
            if owned_group:
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
        finally:
            if not owned_group:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    tasks = [asyncio.create_task(collect(process.stdout)), asyncio.create_task(collect(process.stderr))]
    try:
        async with asyncio.timeout(timeout):
            stdout, _ = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode in unavailable_codes:
            raise deps.error("unavailable", operation)
        if process.returncode != 0:
            raise deps.error("backend_failed", operation)
        return stdout
    except TimeoutError:
        raise deps.error("timeout", operation) from None
    finally:
        await terminate()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def safe_url(value, repository=None):
    if not isinstance(value, str) or len(value) > 1000:
        return None
    prefix = "https://github.com/" + (repository + "/" if repository else "")
    return value if value.startswith(prefix) and not any(c.isspace() for c in value) else None


def sha(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value) else None


def stamp(value):
    from datetime import datetime
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo else None
    except ValueError:
        return None
