"""Read gate scripts, procfs locks and existing tickets; never execute or lock a gate."""
import hashlib
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import Config
from src.models.gates import DeviceQueue, Estimate, Holder, QueueSnapshot, Quiet, Waiter
from src.registry.process import identity

DAY_NS = 86_400_000_000_000
MAX_SCAN = 10000


def _text(path: Path, limit: int = 4096) -> str | None:
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        return data.decode("utf-8") if len(data) <= limit else None
    except (OSError, UnicodeError):
        return None


def _directory(source: str, variable: str) -> Path | None:
    match = re.search(r"^" + variable + r"=\$\{[A-Z0-9_]+:-(/[^}\n]+)\}", source, re.M)
    if not match or any(c in match[1] for c in "$`\"'"):
        return None
    return Path(match[1])


@dataclass(frozen=True)
class Layout:
    script: Path
    directory: Path
    quiet_directory: Path | None
    switch: Path | None
    revision: str


def discover(script: Path | None, device: str) -> Layout | None:
    if script is None:
        return None
    source = _text(script, 131072)
    if source is None:
        return None
    directory = _directory(source, "dir")
    if directory is None:
        return None
    switch = None
    match = re.search(r"^switch=([^\n]+)$", source, re.M)
    if match:
        value = match[1].strip()
        if value.startswith("$dir/"):
            switch = directory / value[5:]
        elif value.startswith("/") and not any(c in value for c in "$`\"' "):
            switch = Path(value)
    return Layout(script, directory, directory if device == "gpu" else _directory(source, "gpu"),
                  switch, hashlib.sha256(source.encode()).hexdigest())


def _key(path: Path) -> tuple[int, int, int] | None:
    try:
        if path.is_symlink():
            return None
        info = path.stat()
        return os.major(info.st_dev), os.minor(info.st_dev), info.st_ino
    except OSError:
        return None


def parse_locks(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        fields = line.split()
        blocked = len(fields) > 1 and fields[1] == "->"
        if blocked:
            fields.pop(1)
        if len(fields) < 8 or fields[1] != "FLOCK":
            continue
        try:
            major, minor, inode = fields[5].split(":")
            rows.append({"key": (int(major, 16), int(minor, 16), int(inode)),
                         "pid": int(fields[4]), "mode": fields[3], "blocked": blocked})
        except ValueError:
            continue
    return rows


class GateReader:
    def __init__(self, config: Config, *, proc_root: Path = Path("/proc"), clock=time.time):
        self.config = config
        self.proc_root = proc_root
        self.clock = clock

    def _command(self, pid: int) -> list[str] | None:
        raw = _text(self.proc_root / str(pid) / "cmdline", 16384)
        return raw.split("\0") if raw is not None else None

    def _gate_process(self, pid: int, layout: Layout) -> tuple[object | None, list[str] | None]:
        birth = identity(pid, self.proc_root)
        command = self._command(pid)
        if birth is None or command is None or not any(Path(arg).name == layout.script.name for arg in command if arg):
            return None, command
        return birth, command

    def _birth_epoch(self, birth) -> float | None:
        text = _text(self.proc_root / "stat", 1048576)
        if text is None:
            return None
        match = re.search(r"^btime (\d+)$", text, re.M)
        return int(match[1]) + birth.start_ticks / os.sysconf("SC_CLK_TCK") if match else None

    def _holder(self, lock, slot: str, layout: Layout | None, observed_at: str, now: float,
                *, resource_class: str | None = None) -> Holder:
        pid = lock["pid"] if lock["pid"] > 0 else None
        birth = identity(pid, self.proc_root) if pid else None
        age = None
        basis = "Lock proves occupancy; its acquisition time is not recorded."
        if layout is not None and pid is not None and birth is not None:
            holder = layout.directory / ("holder." + slot)
            recorded = _text(holder)
            if recorded is not None and recorded.strip() == str(pid):
                try:
                    stamp = holder.stat().st_mtime
                    birth_time = self._birth_epoch(birth)
                    if birth_time is not None and birth_time <= stamp <= now:
                        age = now - stamp
                        basis = "Holder-file mtime matched to the actual lock and current process birth."
                except OSError:
                    pass
        return Holder(observed_at=observed_at, slot=slot, pid=pid,
            process_start_ticks=birth.start_ticks if birth else None, process_alive=True if birth else None,
            resource_class=resource_class, age_seconds=age, age_basis=basis, lock_mode=lock["mode"])

    def _tickets(self, layout, observed_at, now):
        live = []
        stale = 0
        complete = True
        count = 0
        for folder, yielded in (("queue", False), ("yielded", True)):
            path = layout.directory / folder
            if path.is_symlink():
                complete = False
                continue
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_SCAN:
                            complete = False
                            break
                        match = re.fullmatch(r"(\d{1,19})\.(\d+)", entry.name)
                        if not match or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        stamp, pid = map(int, match.groups())
                        birth, command = self._gate_process(pid, layout)
                        if birth is None:
                            stale += 1
                            continue
                        priority = 2 if "--first" in command else 1 if "--priority" in command else 0
                        asked = (stamp + priority * DAY_NS) / 1e9
                        born = self._birth_epoch(birth)
                        # The ticket embeds ordering time, not its file mtime (yielding rewrites it).
                        valid = asked <= now and born is not None and asked >= born - 1
                        if born is not None and asked < born - 1:
                            stale += 1
                            continue
                        resource_class = "exclusive" if "--exclusive" in command else "whole" if "--whole" in command else "ordinary"
                        live.append(Waiter(observed_at=observed_at, ticket=entry.name, pid=pid,
                            process_start_ticks=birth.start_ticks,
                            priority_class=("ordinary", "priority", "first")[priority] if valid else "unknown",
                            resource_class=resource_class, age_seconds=now - asked if valid else None,
                            age_basis="Ticket nanoseconds with command-confirmed priority offset and process birth." if valid else
                                      "Ticket time and current process birth could not be reconciled.", yielded=yielded))
            except FileNotFoundError:
                pass
            except OSError:
                complete = False
        # Duplicate locations can briefly coexist during a yielding transition.
        by_ticket = {}
        for waiter in live:
            by_ticket[waiter.ticket] = waiter
        return list(by_ticket.values()), stale, complete

    def _order(self, waiters, last_kind):
        if any(w.priority_class == "unknown" for w in waiters):
            return None
        ordinary = sorted((w for w in waiters if w.priority_class == "ordinary"), key=lambda w: w.ticket)
        priority = sorted((w for w in waiters if w.priority_class in {"priority", "first"}), key=lambda w: w.ticket)
        order = []
        last = last_kind
        while ordinary or priority:
            special = priority and priority[0].priority_class == "first" and priority[0].resource_class == "exclusive"
            if priority and (special or last != "p" or not ordinary):
                picked = priority.pop(0)
                if not special:
                    last = "p"
            else:
                picked = ordinary.pop(0)
                last = "o"
            order.append(picked.ticket)
        return {ticket: i + 1 for i, ticket in enumerate(order)}

    def snapshot(self, device: str | None = None, request_pid: int | None = None,
                 *, cursor: int = 0, limit: int = 50) -> QueueSnapshot:
        from src.errors import fail
        if device not in {None, "gpu", "intel", "build"} or cursor < 0 or not 1 <= limit <= 100:
            raise fail("invalid_argument", "queue_status")
        now = self.clock()
        observed_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
        layouts = {name: discover(path, name) for name, path in (
            ("gpu", self.config.gpu_gate), ("intel", self.config.intel_gate), ("build", self.config.build_gate))}
        text = _text(self.proc_root / "locks", 1048576)
        locks_known = text is not None
        locks = parse_locks(text or "")
        quiet_dir = next((l.quiet_directory for l in layouts.values() if l and l.quiet_directory), None)
        quiet_key = _key(quiet_dir / "quiet") if quiet_dir else None
        quiet_locks = [r for r in locks if quiet_key and r["key"] == quiet_key and not r["blocked"]]
        wanted = []
        stale_markers = 0
        gpu_layout = layouts["gpu"]
        quiet_complete = True
        if quiet_dir and not quiet_dir.is_symlink():
            try:
                with os.scandir(quiet_dir) as entries:
                    for i, entry in enumerate(entries):
                        if i >= MAX_SCAN:
                            quiet_complete = False
                            break
                        match = re.fullmatch(r"quiet-wanted\.(\d+)", entry.name)
                        if match:
                            pid = int(match[1])
                            if gpu_layout and self._gate_process(pid, gpu_layout)[0] is not None:
                                wanted.append(pid)
                            elif gpu_layout is not None:
                                stale_markers += 1
                            else:
                                quiet_complete = False
            except OSError:
                quiet_complete = False
        quiet_known = locks_known and quiet_key is not None and quiet_complete
        quiet = Quiet(observed_at=observed_at, known=quiet_known,
            holders=[self._holder(r, "quiet", None, observed_at, now) for r in quiet_locks][:100],
            wanted_pids=sorted(wanted)[:100], stale_markers=stale_markers,
            exclusive=any(r["mode"] == "WRITE" for r in quiet_locks) if quiet_known else None)
        devices = []
        for name, layout in layouts.items():
            if device is not None and name != device:
                continue
            basis = ("Conditional alternating-priority order; future arrivals and quiet retries may change it."
                     if name == "gpu" else "This gate has no global ticket/FIFO contract; no position is inferred.")
            base = dict(observed_at=observed_at, device=name, position_basis=basis, eta=Estimate(observed_at=observed_at))
            if layout is None or not layout.directory.is_dir() or layout.directory.is_symlink():
                devices.append(DeviceQueue(**base, status="unavailable", reason="not_configured_or_state_missing"))
                continue
            if not locks_known:
                devices.append(DeviceQueue(**base, status="unavailable", reason="proc_locks_unreadable", source_sha256=layout.revision))
                continue
            slots = ("0", "1") if name == "gpu" else ("0",) if name == "intel" else ("0", "1", "s2", "s3")
            holders = []
            waiters = []
            stale_holders = 0
            for slot in slots:
                key = _key(layout.directory / slot)
                actual = [r for r in locks if key and r["key"] == key and not r["blocked"]]
                recorded = _text(layout.directory / ("holder." + slot))
                if recorded and not any(str(r["pid"]) == recorded.strip() for r in actual):
                    stale_holders += 1
                for lock in actual:
                    same_slots = sum(1 for s in slots if any(r["key"] == _key(layout.directory / s) and r["pid"] == lock["pid"] and not r["blocked"] for r in locks))
                    resource = "exclusive" if name == "gpu" and any(r["pid"] == lock["pid"] and r["mode"] == "WRITE" for r in quiet_locks) else "whole" if name == "gpu" and same_slots == 2 else "ordinary"
                    holders.append(self._holder(lock, slot, layout, observed_at, now, resource_class=resource))
                if name != "gpu":
                    for lock in locks:
                        if key and lock["key"] == key and lock["blocked"] and lock["pid"] > 0:
                            birth = identity(lock["pid"], self.proc_root)
                            if birth:
                                waiters.append(Waiter(observed_at=observed_at, ticket=None, pid=lock["pid"],
                                    process_start_ticks=birth.start_ticks, priority_class="ordinary", resource_class="ordinary",
                                    age_seconds=None, age_basis="Kernel blocked-lock entry has no enqueue timestamp."))
            stale = 0
            complete = True
            position = None
            if name == "gpu":
                waiters, stale, complete = self._tickets(layout, observed_at, now)
                last = _text(layout.directory / "last-kind")
                order = self._order(waiters, last[:1] if last else None) if complete else None
                waiters = [w.model_copy(update={"position_or_null": order[w.ticket] if order else None}) for w in waiters]
                waiters.sort(key=lambda w: (w.position_or_null or MAX_SCAN, w.ticket or ""))
                position = next((w.position_or_null for w in waiters if w.pid == request_pid), None)
            blockers = []
            if (layout.quiet_directory or layout.directory).joinpath("closed").exists() and name in {"gpu", "intel"}:
                blockers.append("card_closed")
            if quiet.exclusive:
                blockers.append("exclusive_quiet_lock")
            if wanted:
                blockers.append("quiet_requested")
            if quiet_locks and any(w.resource_class == "exclusive" for w in waiters):
                blockers.append("shared_quiet_holders")
            if holders:
                blockers.append("occupied_slots")
            if name == "gpu" and holders:
                blockers.append("second_slot_memory_pressure_and_settle_conditions_not_measured")
            if not quiet.known:
                blockers.append("quiet_state_unknown")
            if name != "gpu":
                # Build uses nonblocking lock loops; Intel may already hold its slot while waiting for quiet.
                blockers.append("non_ticket_waiters_not_fully_observable")
            selected = waiters[cursor:cursor + limit]
            devices.append(DeviceQueue(**base, status="available", source_sha256=layout.revision,
                holders=holders, waiters=[w for w in selected if not w.yielded], yielded=[w for w in selected if w.yielded],
                stale_tickets=stale, stale_holder_files=stale_holders, position_or_null=position,
                blocking_reasons=blockers, complete=complete,
                next_cursor=cursor + limit if len(waiters) > cursor + limit else None))
        return QueueSnapshot(observed_at=observed_at, devices=devices, quiet=quiet)
