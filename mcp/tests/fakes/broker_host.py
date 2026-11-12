"""Private test process: real broker host or deliberately invalid local peer."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src import broker
from src.config import Config
from src.registry.process import identity


def record(root, event, **fields):
    fd = os.open(root / "host-events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, json.dumps({"event": event, "pid": os.getpid(), "monotonic_ns": time.monotonic_ns(), **fields}).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


async def serve(config, root, args):
    if args.reject_peer:
        real_credentials = broker.peer_credentials

        def wrong_uid(writer):
            pid, uid, gid = real_credentials(writer)
            record(root, "credential_checked", peer_pid=pid, real_uid=uid)
            return pid, uid + 1, gid

        broker.peer_credentials = wrong_uid
    if args.response_limit is not None:
        broker.MAX_RESPONSE_BYTES = args.response_limit
    await broker.serve(config, idle_seconds=args.idle, lifetime_seconds=args.lifetime)


async def peer(config, root, mode):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signalled(sig):
        record(root, "signal", signal=sig)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signalled, sig)
    birth = identity(os.getpid())
    if mode == "sentinel":
        record(root, "ready", identity=birth.model_dump())
        await stop.wait()
        return

    paths = broker.paths(config)
    paths.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chdir(paths.directory)
    lock_fd = None
    if mode != "foreign":
        lock_fd = os.open(paths.lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths.owner.write_text(json.dumps({"identity": birth.model_dump(), "config_hash": broker.config_hash(config), "protocol": 1}))
        paths.owner.chmod(0o600)

    async def client(reader, writer):
        try:
            raw = await reader.readline()
            if not raw:
                return
            request = json.loads(raw)
            record(root, "request", action=request.get("action"))
            if mode == "drop" and request.get("action") == "call":
                record(root, "mutation_received")
                return
            if mode == "oversized":
                writer.write(b'{"result":"' + b"x" * (broker.MAX_RESPONSE_BYTES + 1) + b'"}\n')
            else:
                writer.write(json.dumps({"result": {"running": True, "identity": birth.model_dump(),
                    "observed_at": "2026-01-01T00:00:00+00:00", "owned_run_ids": [], "active_job_ids": [],
                    "orphaned_runs": [], "orphaned_next_cursor": None, "stale": False, "stopping": False}}).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, ValueError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    server = await asyncio.start_unix_server(client, "control.sock")
    paths.socket.chmod(0o600)
    record(root, "ready", identity=birth.model_dump())
    try:
        await stop.wait()
    finally:
        server.close()
        await server.wait_closed()
        if lock_fd is not None:
            os.close(lock_fd)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("serve", "sentinel", "foreign", "drop", "oversized"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--reject-peer", action="store_true")
    parser.add_argument("--idle", type=float)
    parser.add_argument("--lifetime", type=float)
    parser.add_argument("--response-limit", type=int)
    args = parser.parse_args()
    config = Config.from_env()
    record(args.root, "started", identity=identity(os.getpid()).model_dump())
    if args.mode == "serve":
        await serve(config, args.root, args)
    else:
        await peer(config, args.root, args.mode)


if __name__ == "__main__":
    asyncio.run(main())
