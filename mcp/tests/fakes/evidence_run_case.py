"""Synthetic backend API: no game data and no real profiler dependency."""
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

if Path(__file__).with_name("hang").exists():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    Path(__file__).with_name("pids.json").write_text(json.dumps([os.getpid(), child.pid]))
    while True:
        time.sleep(1)


def whole_lines(path):
    text = Path(path).read_text()
    return text[:text.rfind("\n") + 1]


def present_rows(path):
    return [row for row in csv.reader(whole_lines(path).splitlines()) if row and row[0].isdigit()]


def statistics_of(values):
    return {"mean_ms": statistics.fmean(values), "frames": len(values), "fps": 1000 / statistics.fmean(values)}


def quiet_measure(samples, lo, hi):
    selected = [row for row in samples if lo <= row["t"] <= hi]
    if len(selected) < 4 or selected[-1]["t"] - selected[0]["t"] < .8 * (hi - lo):
        return None
    return {"mean": max(row.get("other", 1) for row in selected), "peak": max(row.get("other", 1) for row in selected)}


def thread_groups(samples, lo, hi):
    first, last = samples[0], samples[-1]
    return {"total": sum(last["threads"][tid][1] - first["threads"][tid][1] for tid in last["threads"]) / 100 / (last["t"] - first["t"])}


def readback_metrics(path, first, last):
    rows = list(csv.DictReader(whole_lines(path).splitlines()))
    rows = [row for row in rows if first <= int(row["frame_begin"]) <= last]
    return {"readbacks_per_frame": len(rows) / (last - first + 1),
            "readback_wait_mean_ms": statistics.fmean(float(row["wait_ms"]) for row in rows) if rows else 0}


def log_metrics(text, first, last):
    import re
    values = re.findall(r"VRAM ([\d.]+) of ([\d.]+) GB", text)
    return ({"vram_gb_max": max(float(a) for a, _ in values), "vram_allowed_gb": float(values[-1][1])} if values else {}), []
