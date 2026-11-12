"""Private Linux guardian. Only Supervisor starts this file; it is not an MCP tool."""
import ctypes
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time


def main():
    spec = json.loads(sys.stdin.buffer.readline(65537))
    parent = int(spec["parent_pid"])
    stopping = False
    reason = "completed"
    def stop(signum, frame):
        nonlocal stopping, reason
        stopping = True
        reason = "cancelled"
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    libc = ctypes.CDLL(None, use_errno=True)
    # Parent death cannot strand work; adopted grandchildren are reaped here.
    if libc.prctl(36, 1, 0, 0, 0) or libc.prctl(1, signal.SIGTERM, 0, 0, 0):
        raise RuntimeError("guardian_setup_failed")
    if os.getppid() != parent:
        stopping = True
    resource.setrlimit(resource.RLIMIT_CORE, (1, 1))
    root = Path(spec["job_dir"])
    child = None if stopping else subprocess.Popen(spec["argv"], cwd=spec["cwd"], stdin=subprocess.DEVNULL)
    began = time.monotonic()
    stop_at = None
    sent = False
    admitted = None
    while True:
        now = time.monotonic()
        if admitted is None:
            try:
                admitted = float((root / "admitted").read_text())
            except FileNotFoundError:
                pass
        if not stopping and ((admitted is not None and now - admitted >= spec["wall_seconds"])
                             or (admitted is None and now - began >= spec["queue_timeout_seconds"])):
            stopping, reason = True, "timeout"
        code = child.poll() if child is not None else None
        if child is None or code is not None:
            stopping = True
        if stopping and not sent:
            stop_at = now
            # The leader catches TERM and retains group ownership through cleanup.
            old_reason = reason
            os.killpg(os.getpgrp(), signal.SIGTERM)
            reason = old_reason
            sent = True
        if stopping and now - stop_at >= spec["grace_seconds"]:
            # Escaped descendants are included via parent links, not name matching.
            rows = {}
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    text = (entry / "stat").read_text()
                    fields = text[text.rindex(")") + 2:].split()
                    rows[int(entry.name)] = (int(fields[1]), int(fields[19]))
                except (OSError, ValueError, IndexError):
                    continue
            owned = {os.getpid()}
            changed = True
            while changed:
                before = len(owned)
                owned.update(pid for pid, (ppid, _) in rows.items() if ppid in owned)
                changed = len(owned) != before
            for pid in owned - {os.getpid()}:
                try:
                    fd = os.pidfd_open(pid)
                    try:
                        text = Path(f"/proc/{pid}/stat").read_text()
                        birth = int(text[text.rindex(")") + 2:].split()[19])
                        if birth == rows[pid][1]:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                    finally:
                        os.close(fd)
                except (ProcessLookupError, FileNotFoundError):
                    pass
        if child is not None:
            code = child.poll()
        if child is None or code is not None:
            # No child can remain after waitpid reports ECHILD; zombies are reaped.
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                break
        time.sleep(0.025)
    receipt = {"exit_code": code, "stop_reason": reason,
               "process_exited": True, "descendants_exited": True,
               "queue_seconds": (admitted - began) if admitted is not None else time.monotonic() - began,
               "held_seconds": time.monotonic() - admitted if admitted is not None else None}
    with (root / "receipt.json").open("x") as stream:
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    main()
