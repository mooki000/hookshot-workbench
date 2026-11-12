"""Input readiness and deadline replies follow the real control bridge contract."""
import asyncio
import json
import pytest
from src.errors import WorkbenchError
from src.models import Job, State
from src.effects.session_bridge import Bridge
from test_session import sandbox, attach, finished, result


@pytest.mark.parametrize("state", ["boot", "intro", "loading", "unknown"])
async def test_input_refuses_before_queueing_or_sending(sandbox, state):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    fake.state = state
    fake.controller_reads = False
    before = len(deps.registry.list_jobs().jobs)
    try:
        reply = await client.call_tool("session_input", {"run_id": run_id,
            "steps": [{"kind": "press", "buttons": ["A"], "duration": 1}],
            "units": "frames", "idempotency_key": "not-ready"}, raise_on_error=False)
        assert reply.is_error
        error = json.loads(reply.content[0].text)
        assert error["operation"] == "session_input_not_ready" and error["retryable"]
        assert "No input was sent" in error["operator_action"]
        assert len(deps.registry.list_jobs().jobs) == before
        assert not any(command["cmd"] == "sequence" for command in fake.commands)
    finally:
        await fake.close()


async def test_game_timeout_reply_is_observed_after_remote_deadline(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    fake.controller_reads = False
    trace = []
    bridge = await Bridge.connect(fake.path)
    try:
        with pytest.raises(WorkbenchError) as failure:
            await bridge.request("sequence", timeout=0.02, steps=[{"at": 0, "frames": 1, "buttons": ["A"]}],
                wait=True, trace=lambda direction, packet: trace.append((direction, packet)))
        assert failure.value.envelope.operation == "session_input_not_consumed"
        assert failure.value.envelope.retryable is True
        assert [direction for direction, _ in trace] == ["request", "reply"]
        assert trace[-1][1]["message"] == "the game did not read the controller"
        assert fake.frame > 10  # Present progress alone never initialized controller input.
        assert len([command for command in fake.commands if command["cmd"] == "sequence"]) == 1
    finally:
        await bridge.close()
        await fake.close()


async def test_partial_input_deadline_remains_uncertain(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    fake.delay = 0.1
    bridge = await Bridge.connect(fake.path)
    try:
        with pytest.raises(WorkbenchError) as failure:
            await bridge.request("sequence", timeout=0.02, steps=[{"at": 0, "frames": 1, "buttons": ["A"]}], wait=True)
        assert failure.value.envelope.code == "uncertain_outcome" and not failure.value.envelope.retryable
    finally:
        await bridge.close()
        await fake.close()


async def test_basic_state_wait_then_input_retains_wire_evidence(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    fake.sampled = False
    fake.state = "boot"
    try:
        waiting = Job.model_validate((await client.call_tool("session_wait", {"run_id": run_id,
            "predicate": {"kind": "state", "state": "menu"}, "stable_frames": 2,
            "timeout_seconds": 2, "idempotency_key": "menu-ready"})).structured_content)
        fake.state = "menu"
        assert (await finished(deps, waiting)).state == State.COMPLETED
        assert result(deps, waiting)["matched"] is True
        assert result(deps, waiting)["sample"]["fresh"] is None
        arguments = {"run_id": run_id, "steps": [{"kind": "press", "buttons": ["A"], "duration": 1}],
            "units": "frames", "timeout_seconds": 1, "idempotency_key": "one-press"}
        job = Job.model_validate((await client.call_tool("session_input", arguments)).structured_content)
        assert (await finished(deps, job)).state == State.COMPLETED
        events = deps.registry.events(job.job_id).events
        outgoing = next(event.data for event in events if event.kind == "bridge_request")
        incoming = next(event.data for event in events if event.kind == "bridge_reply")
        assert outgoing["cmd"] == "sequence" and outgoing["steps"][0]["frames"] == 1
        assert incoming["id"] == outgoing["id"] and incoming["ok"] is True
        assert incoming["end"] - incoming["start"] == 1
        fake.state = "boot"
        old_count = len(fake.commands)
        duplicate = (await client.call_tool("session_input", arguments)).structured_content
        assert duplicate["job_id"] == job.job_id and len(fake.commands) == old_count
    finally:
        await fake.close()
