#!/usr/bin/env python3
"""Synthetic image header writer for the panels argv contract, not visual proof."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

root = Path(__file__).parent
if (root / "hang").exists():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    (root / "pids.json").write_text(json.dumps([os.getpid(), child.pid]))
    while True:
        time.sleep(1)
output = Path(sys.argv[1])
assert not output.exists()
assert len(sys.argv[3:]) % 2 == 0
assert all(Path(path) != output for path in sys.argv[3::2])
(root / "panel-called.json").write_text(json.dumps({"arguments": sys.argv[2:], "width": os.environ["PANEL_W"]}))
output.write_bytes(b"\xff\xd8\xff\xc0" + struct.pack(">HBHH", 17, 8, 2, 2) + b"\0" * 10)
