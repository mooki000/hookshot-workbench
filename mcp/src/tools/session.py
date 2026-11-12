"""Typed, windowless owned sessions. No shell, socket, environment or write-path arguments."""
from typing import Annotated, Literal
from pydantic import Field
from src.effects.session import SessionService
from src.models import Job
from src.models.session import (Cursor, Idempotency, InputStep, Key, Label, Limit, LocalId, OptionsChanged,
    OptionsPage, Predicate, RunId, Sample, SavesPage, ShotResult, Timeout)
from src.policy import Safety, ToolSpec

SPECS = (
    ToolSpec("session_launch", Safety.RUN, "Submit a bounded disposable windowless session through the configured wrapper and gate. Returns a Job; no direct launch."),
    ToolSpec("session_input", Safety.WRITE_LOCAL, "Submit owned typed input. Frames are game presents; hos at is seconds and duration milliseconds, rounded half away from zero at 60 frames/s. Completion is effect_unverified."),
    ToolSpec("session_state", Safety.READ, "Read bounded state with sample age and capability. Missing sampled-frame freshness remains unknown."),
    ToolSpec("session_wait", Safety.READ, "Submit a bounded observed predicate wait as a Job. Cancelling it never stops the game or resends input."),
    ToolSpec("session_shot", Safety.WRITE_LOCAL, "Capture to a new server-chosen run path; return artifact metadata, not image bytes. No overlay.", idempotent=False),
    ToolSpec("session_options_get", Safety.READ, "Read bounded canonical settings with a cursor."),
    ToolSpec("session_options_set", Safety.WRITE_LOCAL, "Set owned-run canonical options, returning before/after. Device, window, microphone and resource-class changes refuse.", idempotent=False),
    ToolSpec("session_teleport", Safety.WRITE_LOCAL, "Submit one debug travel to a bridge-listed destination on a disposable debug profile. No replay on uncertain acceptance."),
    ToolSpec("session_age", Safety.WRITE_LOCAL, "Submit one age change on a disposable debug profile; unavailable commands never fall back to cheats."),
    ToolSpec("session_saves_list", Safety.READ, "Read bounded owned-session save metadata, not contents; consistency during writes is not guaranteed."),
    ToolSpec("save_clone", Safety.WRITE_LOCAL, "Copy a registered save into a new owned directory with a private hash manifest. Never overwrite or modify a library."),
    ToolSpec("session_stop", Safety.WRITE_LOCAL, "Submit owned disposable-session stop; observe game exit before gate release. Save draining is unsupported.", destructive=True),
)


def register(mcp, deps):
    service = SessionService(deps)

    @deps.tool(mcp, "session_launch")
    async def session_launch(build_id: LocalId, profile_id: LocalId, wall_seconds: Annotated[int, Field(ge=1, le=450)],
                             idempotency_key: Idempotency, save_id: LocalId | None = None,
                             capture: Literal["standard", "dense", "none"] = "standard") -> Job:
        return await service.launch_impl(build_id, save_id, profile_id, wall_seconds, capture, idempotency_key)

    @deps.tool(mcp, "session_input")
    async def session_input(run_id: RunId, steps: Annotated[list[InputStep], Field(min_length=1, max_length=64)],
                            units: Literal["frames", "hos"], idempotency_key: Idempotency,
                            timeout_seconds: Timeout = 30) -> Job:
        return await service.input_impl(run_id, steps, units, timeout_seconds, idempotency_key)

    @deps.tool(mcp, "session_state")
    async def session_state(run_id: RunId, max_age_ms: Annotated[int, Field(ge=1, le=10000)] = 500,
                            expected_map: Annotated[str, Field(min_length=1, max_length=80)] | None = None,
                            fields: Annotated[list[Literal["map", "event", "loading", "age"]], Field(max_length=4)] | None = None) -> Sample:
        return await service.state_impl(run_id, max_age_ms, expected_map, fields)

    @deps.tool(mcp, "session_wait")
    async def session_wait(run_id: RunId, predicate: Predicate, idempotency_key: Idempotency,
                           timeout_seconds: Timeout = 30, max_age_ms: Annotated[int, Field(ge=1, le=10000)] = 500,
                           stable_frames: Annotated[int, Field(ge=0, le=600)] = 0) -> Job:
        return await service.wait_impl(run_id, predicate, timeout_seconds, max_age_ms, stable_frames, idempotency_key)

    @deps.tool(mcp, "session_shot")
    async def session_shot(run_id: RunId, after_frame: Annotated[int, Field(ge=0)] | None = None, label: Label = "shot") -> ShotResult:
        return await service.shot_impl(run_id, after_frame, label)

    @deps.tool(mcp, "session_options_get")
    async def session_options_get(run_id: RunId, key: Key | None = None, cursor: Cursor = 0, limit: Limit = 25) -> OptionsPage:
        return await service.options_get_impl(run_id, key, cursor, limit)

    @deps.tool(mcp, "session_options_set")
    async def session_options_set(run_id: RunId,
            values: Annotated[dict[Key, int | float | bool], Field(min_length=1, max_length=8)],
            restore_on_finish: bool = False) -> OptionsChanged:
        return await service.options_set_impl(run_id, values, restore_on_finish)

    @deps.tool(mcp, "session_teleport")
    async def session_teleport(run_id: RunId, destination_id: Annotated[int, Field(ge=0, le=255)],
                               idempotency_key: Idempotency, timeout_seconds: Timeout = 30) -> Job:
        return await service.travel_impl(run_id, "teleport", destination_id, timeout_seconds, idempotency_key)

    @deps.tool(mcp, "session_age")
    async def session_age(run_id: RunId, age: Literal["child", "adult"], idempotency_key: Idempotency,
                          timeout_seconds: Timeout = 30) -> Job:
        return await service.travel_impl(run_id, "age", age, timeout_seconds, idempotency_key)

    @deps.tool(mcp, "session_saves_list")
    async def session_saves_list(run_id: RunId, cursor: Cursor = 0, limit: Limit = 25) -> SavesPage:
        return await service.saves_list_impl(run_id, cursor, limit)

    @deps.tool(mcp, "save_clone")
    async def save_clone(save_id: LocalId, idempotency_key: Idempotency, slot: Annotated[int, Field(ge=0, le=99)] | None = None) -> Job:
        return await service.clone_impl(save_id, slot, idempotency_key)

    @deps.tool(mcp, "session_stop")
    async def session_stop(run_id: RunId, reason: Annotated[str, Field(min_length=1, max_length=160)],
                           idempotency_key: Idempotency) -> Job:
        return await service.stop_impl(run_id, reason, idempotency_key)
