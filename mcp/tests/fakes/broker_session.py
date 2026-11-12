"""Synthetic session subtree with durable child-before-gate observations."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.registry.process import identity, same_process


def record(root, event, **fields):
    value = {"event": event, "monotonic_ns": time.monotonic_ns(), **fields}
    fd = os.open(root / "session-events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, json.dumps(value).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def started(root, role):
    birth = identity(os.getpid())
    record(root, "started", role=role, identity=birth.model_dump())


def gate(root, args):
    started(root, "gate")
    if args and args[0] == "--whole":
        args = args[1:]
    child = subprocess.Popen(args)
    code = child.wait()
    # This is an observation at release, not merely the order of signal sends.
    rows = [json.loads(line) for line in (root / "session-events.jsonl").read_text().splitlines()]
    births = [row["identity"] for row in rows if row["event"] == "started" and row["role"] in {"game", "leaf"}]
    from src.models import ProcessIdentity
    record(root, "gate_released", descendants_exited=bool(births) and all(
        not same_process(ProcessIdentity.model_validate(row)) for row in births))
    return code


def wrapper(root, args):
    assert args[0] == "--inner"
    run = Path(args[1])
    started(root, "wrapper")
    (run / "start.txt").write_text("synthetic start")
    (run / "load-before.txt").write_text("0.1 0.2 0.3")
    child = subprocess.Popen([sys.executable, "-I", __file__, "game", str(root), str(run)])
    code = child.wait()
    record(root, "game_reaped", exit_code=code)
    (run / "exit.txt").write_text(str(code if code >= 0 else 128 - code))
    (run / "end.txt").write_text("synthetic end")
    (run / "load-after.txt").write_text("0.2 0.3 0.4")
    (run / "hookshot_log.txt").write_text("synthetic broker session ended\n")
    (run / "present.csv").write_text("frame,time,ms\n1,1,16\n")
    record(root, "wrapper_receipt")
    return 0


async def leaf(root):
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    started(root, "leaf")
    await stopping.wait()
    record(root, "leaf_exiting")
    return 0


async def game(root, args):
    sys.path.insert(0, str(Path(__file__).parent))
    from session_bridge import FakeBridge
    run = Path(args[0])
    pairs = dict(line.split("=", 1) for line in (run / "extra-env.txt").read_text().splitlines() if "=" in line)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    started(root, "game")
    child = subprocess.Popen([sys.executable, "-I", __file__, "leaf", str(root)])
    bridge = None
    try:
        bridge = await FakeBridge(pairs["HOS_CONTROL"]).start()
        bridge.on_quit = stopping.set
        bridge.delay = 0.25
        record(root, "bridge_ready")
        await stopping.wait()
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        record(root, "leaf_reaped", exit_code=child.returncode)
        if bridge is not None:
            await bridge.close()
    record(root, "game_exiting")
    return 0


if __name__ == "__main__":
    mode, directory, *args = sys.argv[1:]
    root = Path(directory)
    if mode == "game":
        code = asyncio.run(game(root, args))
    elif mode == "leaf":
        code = asyncio.run(leaf(root))
    elif mode == "wrapper":
        code = wrapper(root, args)
    elif mode == "gate":
        code = gate(root, args)
    else:
        raise ValueError("unknown synthetic session role")
    raise SystemExit(code)
