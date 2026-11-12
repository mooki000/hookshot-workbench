"""Synthetic bridge: no game assets, graphics devices, or real gates."""
import asyncio
import json
from pathlib import Path
import struct
import time
import zlib


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data))+kind+data+struct.pack(">I", zlib.crc32(kind+data))
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))+chunk(b"IDAT", zlib.compress(b"\0\x20\x40\x60"))+chunk(b"IEND", b"")


class FakeBridge:
    def __init__(self, path):
        self.path = Path(path)
        self.server = None
        self.frame = 10
        self.map = "SyntheticZone"
        self.age = "child"
        self.loading = False
        self.event = False
        self.sampled = True
        self.stale = False
        self.commands = []
        self.writers = set()
        self.pending = set()
        self.quit_mode = "reply"
        self.closed = asyncio.Event()
        self.delay = 0
        self.age_supported = True
        self.options = {"text_speed": 1, "fps_counter": 0, "music_volume": 5}
        self.on_quit = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self.client, self.path)
        self.path.chmod(0o600)
        return self

    async def close(self):
        if self.server:
            self.server.close()
        for writer in list(self.writers):
            writer.close()
        if self.server:
            await self.server.wait_closed()
        if self.pending:
            for task in list(self.pending):
                task.cancel()
            await asyncio.gather(*list(self.pending), return_exceptions=True)

    async def emit(self, writer, value):
        writer.write(json.dumps(value).encode()+b"\n")
        await writer.drain()

    async def client(self, reader, writer):
        self.writers.add(writer)
        tasks = set()
        try:
            while raw := await reader.readline():
                command = json.loads(raw)
                self.commands.append(command)
                task = asyncio.create_task(self.reply(writer, command))
                tasks.add(task)
                self.pending.add(task)
                task.add_done_callback(tasks.discard)
                task.add_done_callback(self.pending.discard)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.writers.discard(writer)
            writer.close()

    def option(self, key):
        return {"key": key, "value": self.options[key], "min": 0, "max": 10, "step": 1, "restart": False, "fixed": False, "text": "Synthetic"}

    async def reply(self, writer, command):
        cmd, rid = command["cmd"], command["id"]
        self.frame += 1
        response = {"id": rid, "ok": True, "frame": self.frame}
        await self.emit(writer, {"event": "present", "frame": self.frame, "state": "play"})
        if cmd == "status":
            game = {"map": self.map, "age": self.age, "loading": self.loading, "event": self.event}
            if self.sampled:
                game.update(frame=self.frame, t=time.monotonic()-(10 if self.stale else 0))
            response.update(protocol=1, state="play", map=self.map, game=game)
        elif cmd == "wait":
            await asyncio.sleep(0.01)
            self.frame += 1
            response.update(frame=self.frame, waited=1)
        elif cmd in {"sequence", "press", "stick"}:
            await asyncio.sleep(self.delay)
            duration = max((step["at"]+step["frames"] for step in command.get("steps", [])), default=8)
            response.update(start=self.frame, end=self.frame+duration)
        elif cmd == "release":
            response.update(released=1, all=False)
        elif cmd == "shot":
            Path(command["path"]).write_bytes(png())
            response.update(path="/untrusted/ignored.png", width=1, height=1, present=self.frame)
        elif cmd == "option_get":
            key = command.get("name")
            if key is not None:
                if key not in self.options:
                    response.update(ok=False, error="not_found", message="private backend /secret")
                else:
                    response["option"] = self.option(key)
            else:
                response["options"] = [self.option(key) for key in self.options]
        elif cmd == "option_set":
            key = command["name"]
            response["was"] = self.options[key]
            self.options[key] = command["value"]
            response["option"] = self.option(key)
        elif cmd == "teleport":
            if "to" not in command:
                response["destinations"] = [{"index": 0, "child": "SyntheticZone", "adult": "SyntheticZone"}, {"index": 1, "child": "SyntheticAnnex", "adult": "SyntheticAnnex"}]
            else:
                self.map = "SyntheticAnnex" if command["to"] == 1 else "SyntheticZone"
                response.update(sent=True, accepted=True, map=self.map)
        elif cmd == "age":
            if not self.age_supported:
                response.update(ok=False, error="unknown_cmd")
            else:
                self.age = command["to"]
                response.update(sent=True, accepted=True, map=self.map)
        elif cmd == "saves":
            response.update(path="/private/game/saves", slots=[{"slot": str(n).zfill(2), "newest": n == 2,
                "mtime": 100+n, "files": [{"name": "progress.sav", "size": 4, "mtime": 100+n}],
                "caption": {"scene": "not_returned"}} for n in range(3)])
        elif cmd == "quit":
            if self.quit_mode == "silent":
                return
            response["quitting"] = True
        await self.emit(writer, response)
        if cmd == "quit":
            self.closed.set()
            if self.on_quit:
                self.on_quit()
