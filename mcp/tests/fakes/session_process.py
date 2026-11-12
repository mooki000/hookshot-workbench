"""Executable fixture for gate -> wrapper -> bridge -> child process ownership."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def gate(args):
    if args and args[0] == "--whole":
        args = args[1:]
    fixture = Path(os.environ["SESSION_FIXTURE"])
    delay = float((fixture / "gate-delay").read_text())
    deadline = time.monotonic()+delay
    while time.monotonic() < deadline:
        time.sleep(0.01)
    child = subprocess.Popen(args)
    return child.wait()


def wrapper(args):
    assert args[0] == "--inner"
    root = Path(args[1])
    fixture = Path(os.environ["SESSION_FIXTURE"])
    (root / "start.txt").write_text("synthetic start")
    (root / "load-before.txt").write_text("0.1 0.2 0.3")
    child = subprocess.Popen([sys.executable, "-I", __file__, "game", str(root)])
    code = child.wait()
    (root / "exit.txt").write_text(str(code if code >= 0 else 128-code))
    (root / "end.txt").write_text("synthetic end")
    (root / "load-after.txt").write_text("0.2 0.3 0.4")
    (root / "hookshot_log.txt").write_text("synthetic bridge ended\n")
    (root / "present.csv").write_text("frame,time,ms\n1,1,16\n")
    return 0  # deliberately not the game exit code


async def game(args):
    sys.path.insert(0, str(Path(__file__).parent))
    from session_bridge import FakeBridge
    root = Path(args[0])
    fixture = Path(os.environ["SESSION_FIXTURE"])
    pairs = dict(line.split("=", 1) for line in (root / "extra-env.txt").read_text().splitlines() if "=" in line)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    (fixture / "pids.json").write_text(json.dumps({"game": os.getpid(), "child": child.pid}))
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    bridge = await FakeBridge(pairs["HOS_CONTROL"]).start()
    bridge.quit_mode = (fixture / "quit-mode").read_text()
    bridge.on_quit = stopping.set
    try:
        await stopping.wait()
    finally:
        child.terminate()
        child.wait()
        await bridge.close()
    return 0


if __name__ == "__main__":
    mode, *args = sys.argv[1:]
    code = asyncio.run(game(args)) if mode == "game" else wrapper(args) if mode == "wrapper" else gate(args)
    raise SystemExit(code)
