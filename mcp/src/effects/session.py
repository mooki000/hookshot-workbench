"""Reusable owned-session operations; the tool registrar is deliberately thin.

Long operations return registry Jobs. Their result is the bounded ``result``
event; receipts never turn input completion into scene success. Freshness uses
sampled game.frame/game.t, never the bridge envelope frame or draw heuristic.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Awaitable, Callable

from src.errors import WorkbenchError
from src.game_literals import HS_GAME
from src.models import Job, Producer, Receipt, State, TERMINAL, utc_now
from src.models.session import (GameSample, InputResult, InputStep, Option, OptionsChanged, OptionsPage,
    Predicate, PredicateResult, Sample, SaveFile, SaveSlot, SavesPage, ShotResult, StopResult, TravelResult)
from src.effects.session_bridge import Bridge
import sys
from src.effects.session_storage import catalogue, clone_tree, lane_guard, new_directory, plain_files, read_json, write_new
from src.registry import new_id

# Canonical setting keys only. Names/aliases never get a chance to evade this.
DENIED_OPTIONS = frozenset({"fullscreen", "window_mode", "window_size", "monitor", "display", "device", "gpu",
    "output_device", "audio_device", "microphone", "mic", "microphone_device", "voice_chat", "background_input",
    "input_background", "controller", "controller_device", "headless", "output_res", "resolution", "quality",
    "frame_generation", "vram_budget", "memory_budget", "profile", "dlss", "dlss_model", "ultrawide"})
FIELDS = frozenset({"map", "event", "loading", "age"})


def number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    if integer and value != int(value):
        return None
    return int(value) if integer else float(value)


def text(value, bound):
    return value if isinstance(value, str) and len(value) <= bound and not any(ord(c) < 32 for c in value) else None


def boolean(value):
    return value if isinstance(value, bool) else None


def sample_from(reply: dict, *, max_age_ms: int, expected_map: str | None = None,
                selected: list[str] | None = None) -> Sample:
    raw = reply.get("game") if isinstance(reply.get("game"), dict) else {}
    frame = number(raw.get("frame"), integer=True)
    sampled = number(raw.get("t"))
    now = time.monotonic()
    age = (now - sampled) * 1000 if sampled is not None and sampled <= now else None
    fresh = age <= max_age_ms if age is not None and frame is not None else None
    keys = FIELDS if selected is None else set(selected)
    game = GameSample(map=text(raw.get("map") if frame is not None else reply.get("map"), 80) if "map" in keys else None,
        event=boolean(raw.get("event")) if "event" in keys else None,
        loading=boolean(raw.get("loading")) if "loading" in keys else None,
        age=text(raw.get("age"), 32) if "age" in keys else None)
    if game.loading is True or game.event is True or fresh is False or (expected_map and game.map and game.map.casefold() != expected_map.casefold()):
        controllable = False
    elif fresh is True and game.loading is False and game.event is False and (expected_map is None or game.map is not None):
        controllable = True
    else:
        controllable = None
    return Sample(protocol=number(reply.get("protocol"), integer=True), observed_at=utc_now(),
        capabilities={"sampled_state": frame is not None, "freshness": frame is not None and sampled is not None},
        frame=number(reply.get("frame"), integer=True), sample_frame=frame, sample_time=sampled, age_ms=age,
        fresh=fresh, state=text(reply.get("state"), 16), game=game, controllable=controllable,
        unknown_fields=[key for key in sorted(keys) if getattr(game, key) is None] + (["freshness"] if fresh is None else []))


@dataclass
class Session:
    run_id: str
    launch_job: str
    root: Path
    socket: Path
    profile: object
    plan: dict
    process_job: str | None = None
    bridge: Bridge | None = None
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    options_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    restore: dict[str, tuple[Option, Option]] = field(default_factory=dict)
    stopping: bool = False
    stop_reason: str | None = None


class SessionService:
    def __init__(self, deps):
        self.deps = deps
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._closed = False
        deps.services["session"] = self
        deps.add_cleanup(self.close, before_workers=True)

    def active_run_ids(self):
        return [run_id for run_id in self.sessions if self.deps.registry.get_run(run_id).state not in TERMINAL]

    def owned(self, run_id: str, *, active=True) -> Session:
        run = self.deps.registry.get_run(run_id)
        if run.owner != self.deps.registry.owner or run_id not in self.sessions:
            raise self.deps.error("refused_by_policy", "session_owner")
        session = self.sessions[run_id]
        if active and (run.state in TERMINAL or session.stopping):
            raise self.deps.error("unavailable", "session_owner")
        return session

    async def connection(self, session: Session) -> Bridge:
        async with session.connect_lock:
            if session.bridge is None or session.bridge.closed:
                if not session.socket.exists():
                    raise self.deps.error("unavailable", "session_bridge")
                self.deps.paths.read(session.socket)
                session.bridge = await Bridge.connect(session.socket)
            return session.bridge

    async def state_impl(self, run_id: str, max_age_ms=500, expected_map=None, fields=None) -> Sample:
        if not 1 <= max_age_ms <= 10000 or (fields is not None and (len(fields) > 4 or set(fields) - FIELDS)):
            raise self.deps.error("invalid_argument", "session_state")
        bridge = await self.connection(self.owned(run_id))
        return sample_from(await bridge.request("status"), max_age_ms=max_age_ms, expected_map=expected_map, selected=fields)

    def _finish(self, job_id: str, state: State, result=None, error=None, *, reason="completed"):
        if self.deps.registry.get_job(job_id).state in TERMINAL:
            return
        if result is not None:
            payload = result.model_dump() if hasattr(result, "model_dump") else result
            self.deps.registry.append_event(job_id, "result", payload)
        self.deps.registry.finish(job_id, state, Receipt(job_id=job_id, observed_at=utc_now(),
            exit_code=0 if state == State.COMPLETED else None, stop_reason=reason, process_exited=True,
            descendants_exited=True, queue_seconds=None, held_seconds=None), error=error)

    async def _submit(self, operation: str, request: dict, key: str, action: Callable[[str], Awaitable], *, run_id=None,
                      cancel_action=None) -> Job:
        start = getattr(self.deps.supervisor, "start_operation", None)
        if start is None or self._closed:
            raise self.deps.error("unavailable", operation)
        job = self.deps.registry.create_job(operation, request=request, idempotency_key=key)
        if job.job_id in self.deps.supervisor.tasks or job.state in TERMINAL or job.started_at is not None:
            return job
        if run_id:
            self.deps.registry.update_job(job.job_id, run_ids=[run_id])

        async def run():
            self.tasks[job.job_id] = asyncio.current_task()
            try:
                self.deps.registry.update_job(job.job_id, state=State.RUNNING, phase=operation, started_at=utc_now())
                result = await action(job.job_id)
                cancelled = self.deps.registry.cancellation_requested(job.job_id)
                self._finish(job.job_id, State.CANCELLED if cancelled else State.COMPLETED, result,
                    reason="cancelled" if cancelled else "completed")
            except asyncio.CancelledError:
                self._finish(job.job_id, State.CANCELLED, reason="cancelled")
            except WorkbenchError as exc:
                self._finish(job.job_id, State.FAILED, error=exc.envelope, reason=exc.envelope.code)
            except Exception:
                self._finish(job.job_id, State.INTERRUPTED,
                    error=self.deps.error("uncertain_outcome", operation).envelope, reason="uncertain_outcome")
            finally:
                self.tasks.pop(job.job_id, None)

        async def cancel():
            if cancel_action:
                await cancel_action()
            task = self.tasks.get(job.job_id)
            if task and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return await start(job.job_id, run, cancel=cancel)

    async def _input_ready(self, session):
        bridge = await self.connection(session)
        reply = await bridge.request("status", timeout=2)
        frame = number(reply.get("frame"), integer=True)
        state = text(reply.get("state"), 16)
        if state not in {"menu", "play"} or frame is None or frame <= 0 or reply.get("stalled") is True:
            self.deps.registry.append_event(session.launch_job, "input_refused",
                {"state": state, "frame": frame, "sent": False, "reason": "not_ready"})
            raise self.deps.error("unavailable", "session_input_not_ready", retryable=True)

    def _input_trace(self, job_id, direction, packet):
        fields = {}
        for key in ("id", "ok", "frame", "start", "end", "queued", "frames", "timeout", "wait", "exclusive"):
            if key in packet and type(packet[key]) in {int, float, bool}:
                fields[key] = packet[key]
        if packet.get("cmd") in {"sequence", "press", "stick", "release"}:
            fields["cmd"] = packet["cmd"]
        if direction == "request" and "steps" in packet:
            fields["steps"] = packet["steps"]
        if packet.get("error") in {"timeout", "bad_request", "busy", "unavailable", "refused", "cancelled"}:
            fields["error"] = packet["error"]
        if packet.get("message") in {"the game did not read the controller", "the inputs did not finish in time"}:
            fields["message"] = packet["message"]
        self.deps.registry.append_event(job_id, "bridge_" + direction, fields)

    async def input_impl(self, run_id, steps: list[InputStep], units: str, timeout_seconds: float, idempotency_key: str):
        session = self.owned(run_id)
        if not 1 <= len(steps) <= 64 or units not in {"frames", "hos"}:
            raise self.deps.error("invalid_argument", "session_input")
        release = any(step.kind == "release" for step in steps)
        if release and len(steps) != 1:
            raise self.deps.error("invalid_argument", "session_input")
        try:
            converted = [step.bridge_step(units) for step in steps] if not release else []
        except ValueError:
            raise self.deps.error("invalid_argument", "session_input") from None
        request = dict(run_id=run_id, steps=[s.model_dump() for s in steps], units=units, timeout_seconds=timeout_seconds)
        previous = self.deps.registry.find_job("session_input", idempotency_key, request)
        if previous is not None:
            return previous
        if not release:
            await self._input_ready(session)

        async def action(job_id):
            if session.input_lock.locked():
                raise self.deps.error("busy", "session_input")
            async with session.input_lock:
                if not release:
                    await self._input_ready(session)
                bridge = await self.connection(session)
                try:
                    reply = await bridge.request("release" if release else "sequence", timeout=timeout_seconds,
                        trace=lambda direction, packet: self._input_trace(job_id, direction, packet),
                        **({} if release else {"steps": converted, "exclusive": True, "wait": True}))
                except WorkbenchError as exc:
                    if exc.envelope.code == "uncertain_outcome":
                        # Disconnect releases this connection's waited inputs.
                        # Keep the uncertain outcome; do not send them again.
                        await bridge.close()
                    raise
                except asyncio.CancelledError:
                    # This connection owns at most this one action. Never release all.
                    try:
                        await bridge.request("release", timeout=1)
                    except WorkbenchError:
                        await bridge.close()
                    raise
                return InputResult(action_id=job_id, sent=True, queued=number(reply.get("queued"), integer=True),
                    start_frame=number(reply.get("start"), integer=True), end_frame=number(reply.get("end"), integer=True),
                    frame=number(reply.get("frame"), integer=True), completed=True if release or "end" in reply else None)
        return await self._submit("session_input", request, idempotency_key, action, run_id=run_id)

    async def _wait(self, session, predicate: Predicate, timeout_seconds, max_age_ms, stable_frames, job_id=None):
        start = time.monotonic()
        deadline = start + timeout_seconds
        initial, stable_from = None, None
        sample = None
        next_progress = 0.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and sample is not None:
                return PredicateResult(matched=False, sample=sample, frames_waited=None if initial is None or sample.frame is None else sample.frame-initial,
                    elapsed_ms=(time.monotonic()-start)*1000, reason="timeout")
            bridge = await self.connection(session)
            try:
                reply = await bridge.request("status", timeout=max(0.01, min(2, remaining)))
            except WorkbenchError as exc:
                if exc.envelope.code == "timeout" and sample is not None:
                    continue
                raise
            sample = sample_from(reply, max_age_ms=max_age_ms, expected_map=predicate.map)
            if initial is None:
                initial = sample.frame
            if predicate.kind in {"controllable", "field"} and sample.fresh is None:
                return PredicateResult(matched=None, sample=sample, frames_waited=None, elapsed_ms=(time.monotonic()-start)*1000,
                    reason="freshness_unsupported")
            matched = {"frames": sample.frame is not None and initial is not None and sample.frame-initial >= (predicate.frames or 0),
                "frame": sample.frame is not None and sample.frame >= (predicate.frame or 0),
                "state": sample.state == predicate.state,
                "map": sample.game.map is not None and predicate.map is not None and sample.game.map.casefold() == predicate.map.casefold(),
                "controllable": sample.controllable is True,
                "field": sample.fresh is True and getattr(sample.game, predicate.field or "event") == predicate.value}[predicate.kind]
            stability_frame = sample.sample_frame if predicate.kind in {"controllable", "field"} else sample.frame
            if matched:
                if stable_from is None:
                    stable_from = stability_frame
                if stable_frames == 0 or (stability_frame is not None and stable_from is not None and stability_frame-stable_from >= stable_frames):
                    return PredicateResult(matched=True, sample=sample,
                        frames_waited=None if initial is None or sample.frame is None else sample.frame-initial,
                        elapsed_ms=(time.monotonic()-start)*1000, reason="matched")
            else:
                stable_from = None
            if job_id and sample.frame is not None and time.monotonic() >= next_progress:
                self.deps.registry.append_event(job_id, "sample", {"frame": sample.frame, "fresh": sample.fresh,
                    "state": sample.state, "matched": bool(matched)})
                next_progress = time.monotonic() + 1
            # Native present wait is observed progress, not a boot/readiness sleep.
            try:
                await bridge.request("wait", timeout=max(0.01, min(0.25, deadline-time.monotonic())), event="present", frames=1)
            except WorkbenchError as exc:
                if exc.envelope.code != "timeout":
                    raise

    async def wait_impl(self, run_id, predicate, timeout_seconds, max_age_ms, stable_frames, idempotency_key):
        session = self.owned(run_id)
        request = dict(run_id=run_id, predicate=predicate.model_dump(), timeout_seconds=timeout_seconds,
            max_age_ms=max_age_ms, stable_frames=stable_frames)
        return await self._submit("session_wait", request, idempotency_key,
            lambda job_id: self._wait(session, predicate, timeout_seconds, max_age_ms, stable_frames, job_id), run_id=run_id)

    async def shot_impl(self, run_id, after_frame=None, label="shot") -> ShotResult:
        session = self.owned(run_id)
        root = session.root / "bridge-shots"
        if root.is_symlink():
            raise self.deps.error("refused_by_policy", "session_shot")
        if not root.exists():
            new_directory(self.deps, root)
        path = self.deps.paths.output(root / (label + "-" + new_id("shot") + ".png"))
        bridge = await self.connection(session)
        reply = await bridge.request("shot", timeout=10, path=str(path), **({"after": after_frame} if after_frame is not None else {}))
        # Never trust a reply's writable path: inspect only the server's chosen path.
        self.deps.paths.read(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise self.deps.error("refused_by_policy", "session_shot")
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise self.deps.error("backend_failed", "session_shot")
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        if not 0 < width <= 16384 or not 0 < height <= 16384:
            raise self.deps.error("backend_failed", "session_shot")
        artifact = self.deps.registry.add_artifact(path, guards=self.deps.paths, mime="image/png", producer=Producer(tool="session_shot"),
            run_id=run_id, clock="game-present", complete=True)
        run = self.deps.registry.get_run(run_id)
        self.deps.registry.update_run(run_id, artifacts=[*run.artifacts, artifact.id])
        return ShotResult(artifact=artifact, present=number(reply.get("present"), integer=True), frame=number(reply.get("frame"), integer=True),
            width=width, height=height)

    def _option(self, raw) -> Option:
        if not isinstance(raw, dict) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(raw.get("key", ""))):
            raise self.deps.error("backend_failed", "session_options_get")
        def numeric(key):
            v = raw.get(key)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        return Option(key=raw["key"], value=boolean(raw.get("value")) if isinstance(raw.get("value"), bool) else numeric("value"),
            min=numeric("min"), max=numeric("max"), step=numeric("step"), restart=boolean(raw.get("restart")),
            fixed=boolean(raw.get("fixed")), text=text(raw.get("text"), 160))

    async def options_get_impl(self, run_id, key=None, cursor=0, limit=25) -> OptionsPage:
        if not 1 <= limit <= 100 or not 0 <= cursor <= 10000:
            raise self.deps.error("invalid_argument", "session_options_get")
        bridge = await self.connection(self.owned(run_id))
        reply = await bridge.request("option_get", **({"name": key} if key is not None else {}))
        rows = [reply["option"]] if "option" in reply else reply.get("options", [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise self.deps.error("backend_failed", "session_options_get")
        rows = sorted((self._option(r) for r in rows), key=lambda row: row.key)
        return OptionsPage(options=rows[cursor:cursor+limit], next_cursor=cursor+limit if cursor+limit < len(rows) else None, observed_at=utc_now())

    async def options_set_impl(self, run_id, values, restore_on_finish=False) -> OptionsChanged:
        session = self.owned(run_id)
        if not 1 <= len(values) <= 8 or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in values):
            raise self.deps.error("invalid_argument", "session_options_set")
        if any(key in DENIED_OPTIONS or any(part in key for part in ("microphone", "device", "window", "monitor", "display")) for key in values):
            raise self.deps.error("refused_by_policy", "session_options_set")
        if any(not isinstance(v, (int, float, bool)) or not math.isfinite(v) for v in values.values()):
            raise self.deps.error("invalid_argument", "session_options_set")
        if session.options_lock.locked():
            raise self.deps.error("busy", "session_options_set")
        before, after = [], []
        changed = False
        try:
            async with session.options_lock:
                bridge = await self.connection(session)
                async with asyncio.timeout(15):
                    # Validate every canonical key before the first mutation.
                    for key in values:
                        reply = await bridge.request("option_get", name=key)
                        option = self._option(reply.get("option"))
                        if option.key != key or option.fixed is not False:
                            raise self.deps.error("refused_by_policy", "session_options_set")
                        before.append(option)
                    for old in before:
                        changed = True
                        reply = await bridge.request("option_set", name=old.key, value=values[old.key])
                        new = self._option(reply.get("option"))
                        if new.key != old.key:
                            raise self.deps.error("uncertain_outcome", "session_options_set")
                        after.append(new)
                        if restore_on_finish:
                            original = session.restore.get(old.key, (old, new))[0]
                            session.restore[old.key] = (original, new)
        except TimeoutError:
            raise self.deps.error("uncertain_outcome" if changed else "timeout", "session_options_set") from None
        except WorkbenchError:
            if changed:
                raise self.deps.error("uncertain_outcome", "session_options_set") from None
            raise
        restart = True if any(row.restart is True for row in after) else None if any(row.restart is None for row in after) else False
        return OptionsChanged(before=before, after=after, effective=False if restart else True if restart is False else None,
            restart_required=restart, restoration="on_finish" if restore_on_finish else "not_requested")

    async def _restore(self, session):
        if not session.restore:
            return
        bridge = await self.connection(session)
        for key, (old, written) in session.restore.items():
            observed = self._option((await bridge.request("option_get", timeout=1, name=key)).get("option"))
            if observed.value == written.value:
                await bridge.request("option_set", timeout=1, name=key, value=old.value)
        session.restore.clear()

    async def travel_impl(self, run_id, command, destination, timeout_seconds, idempotency_key):
        session = self.owned(run_id)
        if not session.profile.debug:
            raise self.deps.error("refused_by_policy", "session_"+command)
        async def action(job_id):
            bridge = await self.connection(session)
            if command == "teleport":
                listing = await bridge.request("teleport", mutation=False)
                destinations = listing.get("destinations")
                if not isinstance(destinations, list) or len(destinations) > 256:
                    raise self.deps.error("unavailable", "session_teleport")
                if destination not in [row.get("index") for row in destinations if isinstance(row, dict)]:
                    raise self.deps.error("invalid_argument", "session_teleport")
            # Unknown age command is unavailable. Never substitute a cheat or
            # live-file parser, and never repeat a timed-out mutation.
            reply = await bridge.request(command, timeout=min(timeout_seconds, 10), to=destination)
            state = await self.state_impl(run_id)
            sent, accepted = boolean(reply.get("sent")), boolean(reply.get("accepted"))
            arrived = None
            if sent is True and accepted is not False:
                target = text(reply.get("map"), 80)
                predicate = Predicate(kind="controllable", map=target) if command == "teleport" else Predicate(kind="field", field="age", value=destination)
                waited = await self._wait(session, predicate, timeout_seconds, 500, 1, job_id)
                state = waited.sample
                arrived = waited.matched
            return TravelResult(sent=sent, accepted=accepted, arrived=arrived, controllable=state.controllable, state=state)
        return await self._submit("session_"+command, dict(run_id=run_id, destination=destination, timeout_seconds=timeout_seconds),
            idempotency_key, action, run_id=run_id)

    async def saves_list_impl(self, run_id, cursor=0, limit=25) -> SavesPage:
        if not 1 <= limit <= 100 or not 0 <= cursor <= 10000:
            raise self.deps.error("invalid_argument", "session_saves_list")
        bridge = await self.connection(self.owned(run_id))
        reply = await bridge.request("saves")
        raw = reply.get("slots")
        if not isinstance(raw, list) or len(raw) > 1000:
            raise self.deps.error("backend_failed", "session_saves_list")
        slots = []
        for row in raw:
            if not isinstance(row, dict) or not re.fullmatch(r"[0-9]{1,3}", str(row.get("slot", ""))):
                raise self.deps.error("backend_failed", "session_saves_list")
            files = row.get("files", [])
            if not isinstance(files, list) or len(files) > 32:
                raise self.deps.error("backend_failed", "session_saves_list")
            slots.append(SaveSlot(slot=str(row["slot"]), mtime=number(row.get("mtime")), newest=boolean(row.get("newest")),
                files=[SaveFile(name=f["name"], size=number(f.get("size"), integer=True), mtime=number(f.get("mtime")))
                       for f in files if isinstance(f, dict) and text(f.get("name"), 80) and "/" not in f["name"] and "\\" not in f["name"]]))
        slots.sort(key=lambda row: row.slot)
        return SavesPage(slots=slots[cursor:cursor+limit], next_cursor=cursor+limit if cursor+limit < len(slots) else None, observed_at=utc_now())

    def _lane_busy(self, lane, current_job):
        cursor = 0
        while True:
            page = self.deps.registry.list_jobs(active_only=True, cursor=cursor, limit=100)
            for job in page.jobs:
                if job.job_id == current_job:
                    continue
                for run_id in job.run_ids:
                    run = self.deps.registry.get_run(run_id)
                    if run.state not in TERMINAL and run.gate.device == lane:
                        return True
            if page.next_cursor is None:
                return False
            cursor = page.next_cursor

    def _save_source(self, cat, save_id):
        if save_id in cat.saves:
            return cat.saves[save_id].path
        if not re.fullmatch(r"save_[0-9a-f]{32}", save_id):
            raise self.deps.error("not_found", "save_clone")
        root = self.deps.paths.read(cat.clone_root / save_id)
        marker = read_json(root / "clone-identity.json", 1024)
        if marker.get("owner") != self.deps.registry.owner or marker.get("save_id") != save_id:
            raise self.deps.error("refused_by_policy", "save_clone")
        return root / "user"

    async def clone_impl(self, save_id, slot, idempotency_key):
        cat = catalogue(self.deps)
        source = self._save_source(cat, save_id)
        self.deps.paths.read(cat.clone_root)
        if any(cat.clone_root.resolve().is_relative_to(entry.path.resolve()) for entry in cat.saves.values()):
            raise self.deps.error("refused_by_policy", "save_clone")
        plain_files(self.deps, source, slot)
        async def action(job_id):
            clone_id = new_id("save")
            target = cat.clone_root / clone_id
            def progress(count, total):
                if count == total or count % 32 == 0:
                    self.deps.registry.update_job(job_id, completed_steps=count, total_steps=total, phase="copying")
            new_directory(self.deps, target)
            manifest = await clone_tree(self.deps, source, target / "user", slot, progress)
            path = target / "copy-manifest.json"
            write_new(self.deps, path, json.dumps({"source_id": save_id, "save_id": clone_id,
                "owner": self.deps.registry.owner, "files": manifest}, separators=(",", ":")))
            write_new(self.deps, target / "clone-identity.json", json.dumps(
                {"save_id": clone_id, "owner": self.deps.registry.owner}))
            artifact = self.deps.registry.add_artifact(path, guards=self.deps.paths, mime="application/json",
                producer=Producer(tool="save_clone"), complete=True)
            self.deps.registry.update_job(job_id, artifact_uris=[artifact.uri])
            return {"save_id": clone_id, "source_id": save_id, "copy_hash_manifest": artifact.model_dump(),
                "file_count": len(manifest), "provenance": "registered_source_copy", "validation_status": "copy_only_not_a_checkpoint"}
        return await self._submit("save_clone", dict(save_id=save_id, slot=slot), idempotency_key, action)

    async def launch_impl(self, build_id, save_id, profile_id, wall_seconds, capture, idempotency_key):
        deps = self.deps
        cat = catalogue(deps)
        build, profile = cat.builds.get(build_id), cat.profiles.get(profile_id)
        if build is None or profile is None:
            raise deps.error("not_found", "session_launch")
        if not 1 <= wall_seconds <= profile.max_wall_seconds or capture not in {"standard", "dense", "none"}:
            raise deps.error("invalid_argument", "session_launch")
        copy = deps.paths.read(build.path)
        if any(p.is_symlink() for p in (build.path, *build.path.parents)):
            raise deps.error("refused_by_policy", "session_launch")
        # The wrapper writes caches and options alongside the executable.
        deps.paths.output(copy / ("permission-"+new_id("probe")))
        if not os.access(copy / HS_GAME["executable"], os.X_OK) or not (copy / "image.bin").is_file() or not (copy / "romfs").is_dir():
            raise deps.error("unavailable", "session_launch")
        wrapper = deps.config.harness_dir / "hs-run.sh" if deps.config.harness_dir else None
        gate = deps.config.gpu_gate if profile.lane == "gpu" else deps.config.intel_gate
        if any(p is None or not p.is_file() or not os.access(p, os.X_OK) for p in (wrapper, gate)):
            raise deps.error("unavailable", "session_launch")
        if "cancellation" not in inspect.signature(deps.supervisor.start).parameters:
            raise deps.error("unavailable", "session_launch")
        request = dict(build_id=build_id, save_id=save_id, profile_id=profile_id, wall_seconds=wall_seconds, capture=capture)
        plan_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        hold_seconds = wall_seconds + 30  # wrapper TERM/KILL allowance plus cleanup, within 480 seconds
        snapshot = deps.policy.authorize(lane=profile.lane, resource_class=profile.resource_class,
            wall_seconds=hold_seconds, plan_hash=plan_hash, stage="plan")
        save = self._save_source(cat, save_id) if save_id else None
        if save:
            plain_files(deps, save)
        with lane_guard(deps, profile.lane):
            existing = deps.registry.create_job("session_launch", request=request, idempotency_key=idempotency_key)
            if existing.run_ids or existing.state in TERMINAL:
                return existing
            if self._lane_busy(profile.lane, existing.job_id):
                self._finish(existing.job_id, State.FAILED, error=deps.error("busy", "session_launch").envelope, reason="busy")
                raise deps.error("busy", "session_launch")
            run = deps.registry.create_run(state=State.QUEUED, build=build.provenance, config_hash=plan_hash,
                policy_revision=snapshot.revision, gate={"class": profile.resource_class, "device": profile.lane}, timing_valid=False,
                capabilities={"save_drain": False, "disposable": True, "sampled_state": None})
            root = copy / "runs" / run.run_id
            socket = root / "control.sock"
            if len(os.fsencode(socket)) >= 108:
                self._finish(existing.job_id, State.FAILED, error=deps.error("unavailable", "session_launch").envelope)
                deps.registry.update_run(run.run_id, state=State.FAILED, stop_reason="socket_path_too_long")
                raise deps.error("unavailable", "session_launch")
            plan = dict(run_dir=str(root), socket=str(socket), copy=str(copy), wrapper=str(wrapper), gate=str(gate),
                python=sys.executable, state_root=str(deps.config.state_root), lane=profile.lane,
                resource_class=profile.resource_class, wall_seconds=wall_seconds, hold_seconds=hold_seconds, plan_hash=plan_hash)
            session = Session(run.run_id, existing.job_id, root, socket, profile, plan)
            self.sessions[run.run_id] = session
            deps.registry.update_job(existing.job_id, run_ids=[run.run_id])

            async def action(job_id):
                try:
                    await self._launch(session, wall_seconds, capture, save)
                except BaseException:
                    current = deps.registry.get_run(run.run_id)
                    if current.state not in TERMINAL:
                        deps.registry.update_run(run.run_id, state=State.CANCELLED if session.stopping else State.INTERRUPTED,
                            ended_at=utc_now(), stop_reason=session.stop_reason or "launch_interrupted")
                    raise
                return {"run_id": run.run_id, "save_drain": "unsupported", "timing_valid": False}

            async def cancel():
                await self._stop(session)
            return await self._submit("session_launch", request, idempotency_key, action, run_id=run.run_id, cancel_action=cancel)

    async def _launch(self, session, wall_seconds, capture, save):
        deps, root = self.deps, session.root
        if not root.parent.exists():
            new_directory(deps, root.parent)
        new_directory(deps, root)
        new_directory(deps, root / "shots")
        if save:
            await clone_tree(deps, save, root / "user")
        else:
            new_directory(deps, root / "user")
        every = {"standard": 300, "dense": 60, "none": 2147483647}[capture]
        values = {"copy.txt": session.plan["copy"], "seconds.txt": str(wall_seconds), "input.txt": "", "memmax": "",
            "env-file.txt": "# owned empty environment file\n", "extra-env.txt": "\n".join([
                "HOS_CONTROL="+str(session.socket), "HOS_HEADLESS=1", "HOS_OUTPUT_RES=1920x1080",
                "HOS_INPUT_EXCLUSIVE=1", "RX_VK_SHOT_EVERY="+str(every), "RX_VK_SHOT_MENU=1"])+"\n"}
        if session.profile.lane == "intel":
            values["intel"] = ""
        for filename, value in values.items():
            write_new(deps, root / filename, value)
        planfile = root / "session-plan.json"
        write_new(deps, planfile, json.dumps(session.plan))
        process = deps.registry.create_job("session_process", request={"run_id": session.run_id})
        session.process_job = process.job_id
        deps.registry.update_job(process.job_id, run_ids=[session.run_id])
        await deps.supervisor.start(process.job_id, [sys.executable, "-I",
            str(Path(__file__).with_name("session_worker.py")), "--outer", str(planfile)], cwd=Path(session.plan["copy"]),
            wall_seconds=session.plan["hold_seconds"]-20, queued=True, grace_seconds=20, cancellation="adapter")
        admitted, ready, live_log = False, False, False
        while deps.registry.get_job(process.job_id).state not in TERMINAL:
            if not live_log and (root / HS_GAME["log"]).is_file():
                art = deps.registry.add_live_artifact(root / HS_GAME["log"], guards=deps.paths,
                    mime="text/plain", producer=Producer(tool="session_launch"), run_id=session.run_id)
                current = deps.registry.get_run(session.run_id)
                deps.registry.update_run(session.run_id, artifacts=[*current.artifacts, art.id])
                live_log = True
            if not admitted and (root / "admission.json").is_file():
                admission = read_json(root / "admission.json", 1024)
                deps.supervisor.mark_admitted(process.job_id)
                admitted = True
                deps.registry.update_run(session.run_id, state=State.STARTING, started_at=utc_now(), policy_revision=admission["policy_revision"])
                deps.registry.append_event(session.launch_job, "admitted", {"run_id": session.run_id})
            if admitted and session.restore and time.monotonic() - admission["monotonic"] >= max(0, wall_seconds - 5):
                try:
                    await self._restore(session)
                except WorkbenchError:
                    deps.registry.append_event(session.launch_job, "restoration_unconfirmed", {})
                    session.restore.clear()
            if admitted and not ready and session.socket.exists() and not session.stopping:
                try:
                    sample = await self.state_impl(session.run_id)
                    deps.registry.update_run(session.run_id, state=State.RUNNING, capabilities={**sample.capabilities, "save_drain": False, "disposable": True})
                    deps.registry.append_event(session.launch_job, "bridge_ready", {"run_id": session.run_id, "capabilities": sample.capabilities})
                    ready = True
                except WorkbenchError:
                    pass
            await asyncio.sleep(0.05)  # observe admission/receipt files under guardian deadlines
        receipt = deps.registry.receipt(process.job_id)
        if session.restore:
            deps.registry.append_event(session.launch_job, "restoration_unconfirmed", {})
        await self._retain(session)
        game_exit = None
        try:
            raw = (root / "exit.txt").read_bytes()
            if len(raw) <= 16 and re.fullmatch(rb"[0-9]{1,3}\s*", raw):
                game_exit = int(raw)
        except OSError:
            pass
        current = deps.registry.get_run(session.run_id)
        if current.state not in TERMINAL:
            end = State.CANCELLED if session.stopping else State.COMPLETED if receipt and receipt.process_exited and game_exit in {0, 124} else State.FAILED
            if end == State.COMPLETED and current.state in {State.QUEUED, State.STARTING}:
                deps.registry.update_run(session.run_id, state=State.RUNNING)
            deps.registry.update_run(session.run_id, state=end, ended_at=utc_now(), exit=game_exit,
                stop_reason=(session.stop_reason or "stopped") if session.stopping else "game_exit" if game_exit is not None else "missing_game_receipt")
        if session.bridge:
            await session.bridge.close()
        if not receipt or not receipt.process_exited or game_exit is None:
            raise deps.error("uncertain_outcome", "session_launch")
        if game_exit not in {0, 124, 143}:
            raise deps.error("backend_failed", "session_launch")

    async def _retain(self, session):
        run = self.deps.registry.get_run(session.run_id)
        # Seal a new artifact version after exit; retain the old live metadata in
        # the registry, but keep only one current named log in the Run manifest.
        artifacts = [ident for ident in run.artifacts if not (
            (art := self.deps.registry.get_artifact(ident)).producer.tool == "session_launch" and
            not art.complete and self.deps.registry.artifact_path(ident, self.deps.paths).name == HS_GAME["log"])]
        def load_values(filename):
            try:
                raw = (session.root / filename).read_bytes()
                values = [float(value) for value in raw.decode().split()] if len(raw) <= 128 else []
                return values if len(values) == 3 and all(math.isfinite(v) and v >= 0 for v in values) else None
            except (OSError, ValueError, UnicodeError):
                return None
        context = {"load_before": load_values("load-before.txt"), "load_after": load_values("load-after.txt"),
            "quiet": None, "disturbed": None, "source_identity": run.build.model_dump(),
            "instrument_identity": "windowless_wrapper", "settings_identity": run.config_hash,
            "timing_valid": False, "kernel_coverage": None}
        context_path = session.root / "evidence-context.json"
        write_new(self.deps, context_path, json.dumps(context))
        artifact = self.deps.registry.add_artifact(context_path, guards=self.deps.paths, mime="application/json",
            producer=Producer(tool="session_launch"), run_id=session.run_id, complete=True)
        artifacts.append(artifact.id)
        for filename, mime, clock in ((HS_GAME["log"], "text/plain", None), ("frames.csv", "text/csv", "renderer-offscreen-submit"),
                ("present.csv", "text/csv", "game-present"), ("perf.csv", "text/csv", "renderer-offscreen-submit"),
                ("exit.txt", "text/plain", None), ("session-stop.json", "application/json", "host-monotonic")):
            path = session.root / filename
            if path.is_file() and path.stat().st_size <= 64*1024*1024:
                art = self.deps.registry.add_artifact(path, guards=self.deps.paths, mime=mime, producer=Producer(tool="session_launch"),
                    run_id=session.run_id, clock=clock, complete=True)
                artifacts.append(art.id)
        if run.state not in TERMINAL:
            self.deps.registry.update_run(run.run_id, artifacts=artifacts)

    async def _stop(self, session, reason=None) -> StopResult:
        session.stop_reason = session.stop_reason or reason
        session.stopping = True
        job = self.deps.registry.get_job(session.process_job) if session.process_job else None
        if job is None:
            return StopResult(method="cancelled_before_start", process_exited=True, forced=False, artifacts_complete=False)
        if job.state in TERMINAL:
            receipt = self.deps.registry.receipt(job.job_id)
            return StopResult(method="already_exited", process_exited=bool(receipt and receipt.process_exited), forced=False,
                artifacts_complete=(session.root / "exit.txt").is_file())
        if session.bridge is not None and not session.bridge.closed:
            try:
                await session.bridge.request("release", timeout=1)
            except WorkbenchError:
                await session.bridge.close()
        try:
            await self._restore(session)
        except WorkbenchError:
            self.deps.registry.append_event(session.launch_job, "restoration_unconfirmed", {})
        # Core cancellation targets only this adapter. It quits/terminates the
        # owned game before the holder; the generic CPU group path is forbidden.
        await self.deps.supervisor.cancel(job.job_id)
        deadline = time.monotonic()+30
        while self.deps.registry.get_job(job.job_id).state not in TERMINAL and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        receipt = self.deps.registry.receipt(job.job_id)
        if not receipt or not receipt.process_exited:
            raise self.deps.error("uncertain_outcome", "session_stop")
        monitor = self.tasks.get(session.launch_job)
        if monitor is not None and monitor is not asyncio.current_task():
            # Let the owning monitor retain stable artifacts and the actual
            # game's exit before cancellation can mark the Run terminal.
            try:
                await asyncio.wait_for(asyncio.shield(monitor), timeout=3)
            except TimeoutError:
                raise self.deps.error("uncertain_outcome", "session_stop") from None
        try:
            record = read_json(session.root / "session-stop.json", 1024)
        except WorkbenchError:
            record = {"method": "terminate", "forced": True}
        return StopResult(method=record["method"], forced=bool(record["forced"]), process_exited=True,
            artifacts_complete=(session.root / "exit.txt").is_file() and (session.root / "wrapper-receipt.json").is_file())

    async def stop_impl(self, run_id, reason, idempotency_key):
        session = self.owned(run_id, active=False)
        return await self._submit("session_stop", {"run_id": run_id, "reason": reason}, idempotency_key,
            lambda _: self._stop(session, reason), run_id=run_id)

    async def close(self):
        self._closed = True
        launches = {session.launch_job for session in self.sessions.values()}
        pending = []
        for job_id in list(self.tasks):
            if job_id in launches:
                continue
            task = self.deps.supervisor.tasks.get(job_id)
            if task is not None:
                await self.deps.supervisor.cancel(job_id)
                pending.append(task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for session in list(self.sessions.values()):
            if self.deps.registry.get_run(session.run_id).state not in TERMINAL:
                session.stop_reason = session.stop_reason or self.deps.shutdown_reason
            if session.process_job and self.deps.registry.get_job(session.process_job).state not in TERMINAL:
                await self._stop(session, self.deps.shutdown_reason)
            if session.bridge:
                await session.bridge.close()
