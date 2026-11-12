"""Bounded asyncio transport for the trusted control protocol, never a raw MCP tool.

Exactly one task reads each connection. Replies are matched by id (including
out-of-order replies); intervening events are retained in a bounded queue. A
lost mutation reply is uncertain, not grounds to retry or invent a quit reply.
"""
import asyncio
import json
import math
from collections import deque
from pathlib import Path
from src.errors import fail

COMMANDS = frozenset({"status", "press", "stick", "sequence", "release", "shot", "wait", "option_get",
                      "option_set", "teleport", "age", "saves", "quit", "subscribe", "unsubscribe"})
READS = frozenset({"status", "wait", "option_get", "saves", "subscribe", "unsubscribe"})
MAX_REPLY = 524288


class Bridge:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.pending: dict[int, asyncio.Future] = {}
        self.events = deque(maxlen=256)
        self.events_lost = 0
        self.changed = asyncio.Event()
        self.next_id = 0
        self.closed = False
        self._write_lock = asyncio.Lock()
        self._reader_task = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, path: Path, timeout: float = 2):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path), limit=MAX_REPLY), timeout)
            return cls(reader, writer)
        except (OSError, TimeoutError):
            raise fail("unavailable", "session_bridge") from None

    async def _read(self):
        try:
            while line := await self.reader.readline():
                if len(line) > MAX_REPLY:
                    break
                message = json.loads(line, parse_constant=lambda _: None)
                if not isinstance(message, dict):
                    break
                if "event" in message and "ok" not in message:
                    if len(self.events) == self.events.maxlen:
                        self.events_lost += 1
                    self.events.append(message)
                    self.changed.set()
                elif isinstance(message.get("id"), int) and message["id"] in self.pending:
                    future = self.pending.pop(message["id"])
                    if not future.done():
                        future.set_result(message)
        except (OSError, ValueError, asyncio.LimitOverrunError):
            pass
        finally:
            self.closed = True
            self.changed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_result(None)
            self.pending.clear()
            self.writer.close()

    async def request(self, command: str, *, timeout: float = 5, mutation: bool | None = None, **args) -> dict:
        if command not in COMMANDS or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise fail("invalid_argument", "session_bridge")
        operation = "session_" + command
        mutating = command not in READS if mutation is None else mutation
        if self.closed:
            raise fail("unavailable", operation)
        if len(self.pending) >= 32:
            raise fail("busy", operation)
        self.next_id += 1
        rid = self.next_id
        payload = json.dumps({"id": rid, "cmd": command, "timeout": timeout, **args}, allow_nan=False,
                             separators=(",", ":")).encode() + b"\n"
        if len(payload) > 65536:
            raise fail("invalid_argument", operation)
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        sent = False
        try:
            async with asyncio.timeout(timeout):
                async with self._write_lock:
                    sent = True
                    self.writer.write(payload)
                    await self.writer.drain()
                reply = await future
            if reply is None:
                raise fail("uncertain_outcome" if mutating else "unavailable", operation)
            if reply.get("ok") is not True:
                code = reply.get("error")
                mapped = {"unknown_cmd": "unavailable", "unavailable": "unavailable", "busy": "busy",
                          "refused": "refused_by_policy", "forbidden": "refused_by_policy", "not_found": "not_found",
                          "bad_request": "invalid_argument", "timeout": "uncertain_outcome" if mutating else "timeout"}
                raise fail(mapped.get(code, "uncertain_outcome" if mutating else "backend_failed"), operation)
            return reply
        except (TimeoutError, OSError):
            raise fail("uncertain_outcome" if mutating and sent else "timeout", operation) from None
        finally:
            self.pending.pop(rid, None)
            if not future.done():
                future.cancel()

    def drain_events(self) -> list[dict]:
        events = list(self.events)
        self.events.clear()
        self.changed.clear()
        return events

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except OSError:
            pass
        if not self._reader_task.done():
            self._reader_task.cancel()
        await asyncio.gather(self._reader_task, return_exceptions=True)
