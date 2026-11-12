"""Linux process birth identity; zombies are not running work."""
from pathlib import Path
from src.models import ProcessIdentity


def identity(pid: int, proc_root: Path = Path("/proc")) -> ProcessIdentity | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text()
        fields = text[text.rindex(")") + 2:].split()
        if fields[0] in {"Z", "X"}:
            return None
        return ProcessIdentity(pid=pid, start_ticks=int(fields[19]),
            process_group=int(fields[2]), session_id=int(fields[3]),
            boot_id=(proc_root / "sys/kernel/random/boot_id").read_text().strip())
    except (OSError, ValueError, IndexError):
        return None


def same_process(expected: ProcessIdentity) -> bool:
    return identity(expected.pid) == expected
