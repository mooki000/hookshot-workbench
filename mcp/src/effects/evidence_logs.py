"""Whole-line log cursors and conservative retained fault classification."""
import asyncio
import base64
from datetime import datetime
import json
from pathlib import Path
from src.models import TERMINAL
from src.models.evidence import LogPage
from src.effects.evidence_common import configured, named_run_artifact, redact
from src.effects.evidence_signatures import FILTERS, FATAL, DEVICE_LOST, classify_kernel

SCAN_CAP = 1024 * 1024


def _cursor(art, stat, offset, skipping, signature):
    data = [art.id, stat.st_dev, stat.st_ino, offset, skipping, signature]
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def run_logs_impl(deps, run_id, cursor=None, last_lines=None, byte_cap=16384, signature=None, limit=100):
    operation = "run_logs"
    if (cursor is not None and last_lines is not None) or not 256 <= byte_cap <= 65536 or not 1 <= limit <= 500:
        raise deps.error("invalid_argument", operation)
    if last_lines is not None and not 1 <= last_lines <= 500:
        raise deps.error("invalid_argument", operation)
    if signature is not None and signature not in FILTERS:
        raise deps.error("invalid_argument", operation)
    run = deps.registry.get_run(run_id)
    art, path = named_run_artifact(deps, run, {"hookshot_log.txt"}, operation)
    if art.mime != "text/plain":
        raise deps.error("refused_by_policy", operation)
    stat = path.stat()
    offset, skipping = 0, False
    if cursor is not None:
        try:
            if len(cursor) > 512:
                raise ValueError
            aid, device, inode, offset, skipping, selected = json.loads(base64.urlsafe_b64decode(cursor))
            if (aid != art.id or device != stat.st_dev or inode != stat.st_ino or selected != signature or
                type(offset) is not int or not 0 <= offset <= stat.st_size or type(skipping) is not bool):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise deps.error("invalid_argument", operation) from None
    tail_partial = False
    if last_lines is not None:
        offset = max(0, stat.st_size - SCAN_CAP)
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(SCAN_CAP)
        if offset:
            end = raw.find(b"\n")
            if end < 0:
                return LogPage(lines=[], next_cursor=_cursor(art, stat, stat.st_size, True, signature),
                    truncated=True, complete=False, omitted_lines=1)
            offset += end + 1
            raw = raw[end + 1:]
            tail_partial = True
        chunks = raw.splitlines(keepends=True)
        whole = chunks if not chunks or chunks[-1].endswith(b"\n") else chunks[:-1]
        offset += sum(len(line) for line in whole[:-last_lines])
        limit = min(limit, last_lines)
    lines, used, scanned, redactions, omitted = [], 0, 0, 0, 0
    truncated = tail_partial
    partial = False
    with path.open("rb") as stream:
        stream.seek(offset)
        if offset and not skipping and last_lines is None:
            stream.seek(offset - 1)
            if stream.read(1) != b"\n":
                raise deps.error("invalid_argument", operation)
        while scanned < SCAN_CAP and len(lines) < limit:
            start = stream.tell()
            raw = stream.readline(min(65537, SCAN_CAP - scanned))
            if not raw:
                break
            scanned += len(raw)
            if skipping:
                offset = stream.tell()
                skipping = not raw.endswith(b"\n")
                truncated = True
                continue
            if not raw.endswith(b"\n"):
                if len(raw) >= 65537 or scanned >= SCAN_CAP:
                    omitted += 1
                    skipping = True
                    offset = stream.tell()
                    truncated = True
                    continue
                offset = start
                partial = True
                break
            text = raw[:-1].decode("utf-8", "replace").rstrip("\r")
            if signature and not FILTERS[signature].search(text):
                offset = stream.tell()
                continue
            text, count = redact(text)
            size = len(text.encode()) + 1
            if size > byte_cap:
                offset = stream.tell()
                omitted += 1
                truncated = True
                continue
            if used + size > byte_cap:
                offset = start
                truncated = True
                break
            lines.append(text)
            used += size
            redactions += count
            offset = stream.tell()
    at_end = offset >= stat.st_size
    truncated = truncated or offset < stat.st_size or skipping
    complete = at_end and not partial and not skipping and run.state in TERMINAL and art.complete
    token = None if complete else _cursor(art, stat, offset, skipping, signature)
    return LogPage(lines=lines, next_cursor=token, truncated=truncated, complete=complete,
                   redactions=redactions, omitted_lines=omitted)


def _whole_prefix(path: Path, cap):
    with path.open("rb") as stream:
        raw = stream.read(cap + 1)
    bounded = raw[:cap]
    whole = bounded[:bounded.rfind(b"\n") + 1]
    return whole.decode("utf-8", "replace").splitlines(), len(raw) <= cap and len(whole) == len(raw)


def _source_lines(lines, max_lines=100, byte_cap=32768):
    output, used, redactions = [], 0, 0
    for line in lines:
        line, count = redact(line)
        if len(line.encode()) > 2048:
            continue  # Never present a cut line as a whole source line.
        size = len(line.encode()) + 1
        if len(output) >= max_lines or used + size > byte_cap:
            break
        output.append(line)
        used += size
        redactions += count
    return output, len(output) < len(lines), redactions


async def _journal(deps, run):
    if getattr(deps.config, "journal_reader", None) is None or not run.started_at or not run.ended_at:
        return None, "unknown"
    try:
        start, end = datetime.fromisoformat(run.started_at), datetime.fromisoformat(run.ended_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            return None, "unknown"
        reader = configured(deps, "journal_reader", "run_faults")
        process = await asyncio.create_subprocess_exec(str(reader), "-k", "--since", start.isoformat(),
            "--until", end.isoformat(), "--no-pager", "-o", "short-iso",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(5):
                raw = bytearray()
                while len(raw) <= SCAN_CAP:
                    chunk = await process.stdout.read(min(65536, SCAN_CAP + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > SCAN_CAP:
                    process.kill()
                    await process.wait()
                    return None, "unknown"
                await process.wait()
                if process.returncode != 0:
                    return None, "unknown"
                return raw.decode("utf-8", "replace").splitlines(), "complete"
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    except (OSError, ValueError, TimeoutError):
        return None, "unknown"


async def run_faults_impl(deps, run_id):
    run = deps.registry.get_run(run_id)
    art, path = named_run_artifact(deps, run, {"hookshot_log.txt"}, "run_faults")
    if art.mime != "text/plain":
        raise deps.error("refused_by_policy", "run_faults")
    lines, covered = _whole_prefix(path, 4 * SCAN_CAP)
    fatal = [line for line in lines if FATAL.search(line)]
    lost = [line for line in lines if DEVICE_LOST.search(line)]
    retained = named_run_artifact(deps, run, {"kernel.txt"}, "run_faults", required=False)
    if retained is not None:
        kernel_lines, kernel_full = _whole_prefix(retained[1], SCAN_CAP)
        kernel_status = "complete" if kernel_full and retained[0].complete else "partial"
    else:
        kernel_lines, kernel_status = await _journal(deps, run)
    failed = True if fatal or lost or run.state.value == "failed" else (False if run.state.value == "completed" else None)
    if kernel_lines is None:
        kernel = {name: None for name in ("known", "unknown", "faults", "repeats", "mapping_refusals", "allocation_lines", "allocation_events")}
        kernel_stop, sources = None, []
    else:
        kernel = classify_kernel(kernel_lines, failed)
        kernel_stop, sources = kernel.pop("stop_required"), kernel.pop("lines")
    stop = True if fatal or lost or kernel_stop is True else (False if covered and kernel_status == "complete" and kernel_stop is False else None)
    source_lines, truncated, redactions = _source_lines(fatal + lost + sources)
    return {"run_id": run_id, "fatal": len(fatal), "device_lost": len(lost),
            "kernel": {**kernel, "status": kernel_status}, "stop_required": stop,
            "source_lines": source_lines, "source_lines_truncated": truncated, "redactions": redactions,
            "coverage": {"log": "complete" if covered and art.complete and run.state in TERMINAL else "partial",
                         "kernel": kernel_status, "started_at": run.started_at, "ended_at": run.ended_at}}
