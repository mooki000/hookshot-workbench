"""Shared, framework-independent checks operations and conservative parsers."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import subprocess
import time
from src.errors import fail
from src.models.checks import Catalogue, PreflightLine, PreflightResult, TestResult, WindowsResult

MAX_BYTES = 8 * 1024 * 1024
TEST = r"(?:tests|nx2)/test_[A-Za-z0-9_.-]+\.py"
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        before = path.stat()
        value = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise fail("busy", "checks_identity")
    return value


def json_read(path: Path, limit=MAX_BYTES):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise fail("backend_failed", "checks_read")
    return json.loads(raw)


def catalogue(deps) -> Catalogue:
    path = getattr(deps.config, "checks_catalogue", None)
    if path is None:
        raise deps.error("unavailable", "checks_catalogue")
    if path.is_symlink():
        raise deps.error("refused_by_policy", "checks_catalogue")
    path = deps.paths.read(path)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise deps.error("refused_by_policy", "checks_catalogue")
    return Catalogue.model_validate(json_read(path, 262144))


def prerequisite(deps, path: Path | None, *, directory=False) -> Path:
    if path is None:
        raise deps.error("unavailable", "checks_prerequisite")
    result = deps.paths.read(path)
    if not (result.is_dir() if directory else result.is_file()):
        raise deps.error("unavailable", "checks_prerequisite")
    return result


def capture(argv, *, cwd: Path, env=None, timeout=30, limit=MAX_BYTES):
    """Bound output while it arrives, and never replay a failed command."""
    proc = subprocess.Popen([str(x) for x in argv], cwd=cwd, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True)
    result = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(proc.stdout, selectors.EVENT_READ)
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise fail("timeout", "checks_backend")
                for key, _ in ready.select(min(remaining, 0.2)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        ready.unregister(key.fileobj)
                        continue
                    result.extend(chunk)
                    if len(result) > limit:
                        raise fail("backend_failed", "checks_output_limit")
            code = proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            return code, result.decode("utf-8", errors="replace")
    finally:
        # Never signal a reaped PID: its numeric identity may already be reused.
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()
        proc.stdout.close()


def git(cat, root, *args):
    code, text = capture([cat.git, "-c", "core.hooksPath=/dev/null", "-C", root, *args], cwd=root,
                         env={"PATH": os.defpath, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
    if code:
        raise fail("backend_failed", "checks_git")
    return text.strip()


def checkout(deps, cat, repo_id, commit):
    entry = cat.checkouts.get(repo_id)
    if entry is None:
        raise deps.error("not_found", "checks_checkout")
    root = prerequisite(deps, entry.path, directory=True)
    if commit not in entry.reviewed_commits:
        raise deps.error("refused_by_policy", "checks_review")
    if git(cat, root, "rev-parse", commit + "^{commit}") != commit:
        raise deps.error("refused_by_policy", "checks_commit")
    # Archive materialization is deliberately bounded to ordinary, fully vendored trees.
    # A gitlink requires a separately reviewed materialization, never an implicit network update.
    rows = git(cat, root, "ls-tree", "-r", commit).splitlines()
    if any(row.startswith("160000 ") for row in rows):
        raise deps.error("unavailable", "checks_submodules")
    return root


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name:
        raise fail("refused_by_policy", "checks_relative_path")
    return path


def safe_text(text, limit=500):
    text = "".join(c for c in ANSI.sub("", text) if c == "\t" or ord(c) >= 32)
    text = re.sub(r"(?<![\w])(?:/[\w.~-]+){2,}[^\s]*", "[local-path]", text)
    text = re.sub(r"(?i)\b(?:token|password|secret|authorization)\s*[:=]\s*\S+", "[redacted]", text)
    return text[:limit]


def parse_source(text):
    """Only the final runner table is authority, not duplicated streaming/failure output."""
    lines = ANSI.sub("", text).splitlines()
    start = next((i + 1 for i in range(len(lines) - 1, -1, -1)
                  if re.fullmatch(r"\s*result\s+time\s+check\s*", lines[i])), None)
    rows, totals = {}, None
    if start is not None:
        for line in lines[start:]:
            match = re.match(r"\s*(pass|FAIL|TIMEOUT|skip)\s+(?:(\d+(?:\.\d+)?)s\s+)?(" + TEST + r")(?:\s|$)", line)
            if match:
                status, seconds, name = match.groups()
                rows[name] = TestResult(name=name, status={"pass": "passed", "skip": "skipped"}.get(status, "failed"),
                                        seconds=float(seconds) if seconds else None)
            end = re.match(r"(\d+) passed, (\d+) failed, (\d+) skipped\b", line)
            if end:
                totals = tuple(map(int, end.groups()))
    counts = tuple(sum(row.status == status for row in rows.values()) for status in ("passed", "failed", "skipped"))
    complete = totals is not None and totals == counts
    return list(rows.values()), totals if complete else (None, None, None), complete


def dry_run(text):
    missing = re.search(r"^dry run: (\d+) files to compile besides the lift's, (\d+) missing$", text, re.M)
    keys = re.search(r"^object keys: game-code headers ([0-9a-f]+), flags ([0-9a-f]+), hot flags ([0-9a-f]+),", text, re.M)
    return {"files": int(missing[1]) if missing else None, "missing": int(missing[2]) if missing else None,
            "headers": keys[1] if keys else None, "flags": keys[2] if keys else None, "hot_flags": keys[3] if keys else None}


def parse_pr(text, code):
    lines = []
    for line in text.splitlines():
        match = re.match(r"^(INFO|OK|WARN|FAIL)\s+(.+)$", line)
        if match:
            lines.append(PreflightLine(level=match[1], text=safe_text(match[2])))
    terminal = text.rstrip().splitlines()[-1:]
    passed = (code == 0 and terminal == ["PASS"]) if terminal in (["PASS"], ["FAIL"]) else None
    base = re.search(r"origin/main is ([0-9a-f]+)", text)
    fetch = "failed" if "could not fetch origin" in text else "completed" if base else "unknown"
    return PreflightResult(checks=lines[:100], passed=passed, fetch_status=fetch,
                           base_observed=base[1] if base else None, truncated=len(lines) > 100)


def parse_windows(report, code):
    records = [r for test in report.get("tests", []) for r in test.get("records", [])]
    commands = [[safe_text(str(arg), 200) for arg in row.get("command", [])[:100]] for row in records]
    return WindowsResult(compiled=sum(r.get("status") == "PASS" for r in records),
        failed=sum(r.get("status") == "FAIL" for r in records) + sum(t.get("empty_environment_contract", {}).get("status") == "FAIL" for t in report.get("tests", [])),
        unjudged=len(report.get("unjudged", [])) + sum(r.get("status") == "UNJUDGED" for r in records),
        commands=commands[:100], commands_truncated=len(commands) > 100, exit=code)
