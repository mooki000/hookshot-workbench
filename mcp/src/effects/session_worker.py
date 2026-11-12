"""Private supervised game adapter; never registered as an executable MCP surface.

The outer worker owns one configured gate. The admitted worker rereads operator
policy, then uses the trusted wrapper's internal run phase. It never executes a
game itself. Cancellation ends the wrapper's child subtree before the gate.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

# -I deliberately ignores caller PYTHONPATH; import only this installed package.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import Config
from src.effects.session_bridge import Bridge
from src.policy import Policy
from src.registry.process import identity, same_process


def load(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("plan_bound")
    return json.loads(raw)


def record(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def tree(root: int):
    rows = {}
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            fields = (item / "stat").read_text().rsplit(")", 1)[1].split()
            rows[int(item.name)] = (int(fields[1]), identity(int(item.name)))
        except (OSError, ValueError, IndexError):
            pass
    depths = {root: 0}
    while True:
        found = {pid: depths[parent] + 1 for pid, (parent, _) in rows.items() if parent in depths and pid not in depths}
        if not found:
            break
        depths.update(found)
    return [(rows[pid][1], depth) for pid, depth in depths.items() if pid in rows and rows[pid][1] is not None]


def send(birth, sig):
    if birth is None or not same_process(birth):
        return
    try:
        fd = os.pidfd_open(birth.pid)
        try:
            if same_process(birth):
                signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)
    except ProcessLookupError:
        pass


async def terminate_wrapper(birth):
    """Leave the gate and admitted adapter alive while owned game descendants end."""
    if birth is None or not same_process(birth):
        return
    descendants = sorted(tree(birth.pid), key=lambda row: row[1], reverse=True)
    for child, depth in descendants:
        if depth:
            send(child, signal.SIGTERM)
    deadline = time.monotonic() + 2
    while any(same_process(child) for child, depth in descendants if depth) and time.monotonic() < deadline:
        await asyncio.sleep(0.025)
    for child, depth in descendants:
        if depth:
            send(child, signal.SIGKILL)
            deadline = time.monotonic() + 1
            while same_process(child) and time.monotonic() < deadline:
                await asyncio.sleep(0.025)
    # Wrapper normally finishes its receipt after timeout/game death. Give that
    # observed exit a bounded chance before terminating an unresponsive wrapper.
    deadline = time.monotonic() + 2
    while same_process(birth) and time.monotonic() < deadline:
        await asyncio.sleep(0.025)
    if same_process(birth):
        send(birth, signal.SIGTERM)
        await asyncio.sleep(0)


async def held(plan: dict) -> int:
    root = Path(plan["run_dir"])
    try:
        policy = Policy(Config(state_root=Path(plan["state_root"])))
        snapshot = policy.authorize(lane=plan["lane"], resource_class=plan["resource_class"],
            wall_seconds=plan["hold_seconds"], plan_hash=plan["plan_hash"], stage="admission")
    except Exception:
        record(root / "admission-refused.json", {"code": "refused_by_policy"})
        return 3
    record(root / "admission.json", {"monotonic": time.monotonic(), "policy_revision": snapshot.revision})
    child = await asyncio.create_subprocess_exec(plan["wrapper"], "--inner", str(root),
        cwd=plan["copy"], stdin=asyncio.subprocess.DEVNULL)
    birth = identity(child.pid)
    if birth is None:
        return 4
    record(root / "wrapper-process.json", birth.model_dump())
    code = await child.wait()
    # A wrapper returning 0 is not proof of game success: game exit.txt is read
    # separately by the session service. This records only actual child exit.
    record(root / "wrapper-receipt.json", {"exit_code": code, "process_exited": True})
    return code


async def outer(plan: dict, path: Path) -> int:
    from src.models import ProcessIdentity
    root = Path(plan["run_dir"])
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    command = [plan["gate"]]
    if plan["resource_class"] == "whole":
        command.append("--whole")
    command.extend([plan["python"], "-I", str(Path(__file__).resolve()), "--held", str(path)])
    child = await asyncio.create_subprocess_exec(*command, cwd=plan["copy"],
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
    gate_birth = identity(child.pid)
    forced, method = False, "already_exited"
    exiting = asyncio.create_task(child.wait())
    stopping_task = asyncio.create_task(stopping.wait())

    async def wall_bound():
        while not stopping.is_set():
            try:
                admitted = load(root / "admission.json")["monotonic"]
            except FileNotFoundError:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=0.05)
                except TimeoutError:
                    pass
                continue
            remaining = admitted + plan["wall_seconds"] - time.monotonic()
            if remaining > 0:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=remaining)
                except TimeoutError:
                    pass
            stopping.set()
            return

    wall_task = asyncio.create_task(wall_bound())
    try:
        done, _ = await asyncio.wait((exiting, stopping_task), return_when=asyncio.FIRST_COMPLETED)
        if stopping_task in done and not exiting.done():
            bridge = None
            try:
                bridge = await Bridge.connect(Path(plan["socket"]), timeout=0.5)
                try:
                    await bridge.request("release", timeout=0.5)
                except Exception:
                    pass
                method = "quit"
                try:
                    await bridge.request("quit", timeout=1)
                except Exception:
                    pass  # A missing quit reply never proves or disproves exit.
            except Exception:
                pass
            finally:
                if bridge:
                    await bridge.close()
            try:
                await asyncio.wait_for(asyncio.shield(exiting), timeout=3)
            except TimeoutError:
                forced = True
                try:
                    birth = ProcessIdentity.model_validate(load(root / "wrapper-process.json"))
                except (OSError, ValueError):
                    birth = None
                if birth is not None:
                    method = "terminate"
                    await terminate_wrapper(birth)
                elif not (root / "admission.json").exists():
                    method = "cancelled_before_start"
                    # No wrapper was admitted. End the queued gate's exact
                    # subtree, never unlink its tickets or holder files.
                    for owned, _ in sorted(tree(child.pid), key=lambda row: row[1], reverse=True):
                        send(owned, signal.SIGTERM)
                # The gate is last, and only when wrapper/process exit was
                # observed. Otherwise the guardian retains ownership/escalates.
                try:
                    await asyncio.wait_for(asyncio.shield(exiting), timeout=5)
                except TimeoutError:
                    if birth is None and not (root / "admission.json").exists():
                        send(gate_birth, signal.SIGKILL)
                    await exiting
        code = await exiting
        record(root / "session-stop.json", {"method": method, "forced": forced, "process_exited": True})
        return code
    finally:
        stopping_task.cancel()
        wall_task.cancel()
        await asyncio.gather(stopping_task, wall_task, return_exceptions=True)


def main():
    try:
        mode, filename = sys.argv[1:]
        path = Path(filename)
        plan = load(path)
        code = asyncio.run(held(plan) if mode == "--held" else outer(plan, path))
    except Exception:
        # Private logs also avoid exception payloads from untrusted responses.
        print("session adapter failed; reconcile the owned attempt", file=sys.stderr)
        code = 5
    raise SystemExit(code)


if __name__ == "__main__":
    main()
