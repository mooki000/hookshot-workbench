"""Small header-only synthetic retained capture backend."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

W, H, SIZE, MASK = 2, 2, 32, 2**32 - 1

if Path(__file__).with_name("hang").exists():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    Path(__file__).with_name("pids.json").write_text(json.dumps([os.getpid(), child.pid]))
    while True:
        time.sleep(1)


def capture_info(path):
    raw = Path(path).read_bytes()
    if len(raw) % SIZE or len(raw) < 3 * SIZE:
        raise ValueError("invalid capture")
    rows = [struct.unpack_from("<QII", raw, offset) for offset in range(0, len(raw), SIZE)]
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(rows, rows[1:])):
        raise ValueError("nonadvancing capture")
    sequences = [row[1] for row in rows]
    missing = sum(b - a - 1 for a, b in zip(sequences, sequences[1:]))
    return SimpleNamespace(timestamps=[row[0] for row in rows], sequences=sequences,
        kinds=[row[2] for row in rows], missing=missing,
        centres=[i for i in range(1, len(rows) - 1) if sequences[i] - sequences[i-1] == sequences[i+1] - sequences[i] == 1])


if __name__ == "__main__":
    info = capture_info(sys.argv[1])
    print(f"{len(info.sequences)} frames")
    print("events: 1 on the game's frames, 0 on made frames")
    print("frame 1 (game) brighter 2 blocks, peak 20, at 1.00 s")
