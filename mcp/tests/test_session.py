import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import pytest
from fastmcp import Client
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError
from src.main import build_server
from src.models import State, TERMINAL
from src.models.session import InputStep, Predicate, SessionProfile
from src.effects.session import SessionService, Session, sample_from
from src.effects.session_bridge import Bridge
from src.effects.session_storage import clone_tree
from src.registry.process import identity
from src.tools import registry as tool_registry, session as tools

SPEC = importlib.util.spec_from_file_location("fake_session_bridge", Path(__file__).parent / "fakes/session_bridge.py")
FAKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FAKE)


async def observed(predicate, seconds=10):
    end = time.monotonic()+seconds
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError("expected observed state did not arrive")
        await asyncio.sleep(0.01)


async def finished(deps, job):
    await observed(lambda: deps.registry.get_job(job.job_id).state in TERMINAL, 35)
    return deps.registry.get_job(job.job_id)


def result(deps, job):
    cursor, found = 0, None
    while True:
        page = deps.registry.events(job.job_id, cursor=cursor, limit=100)
        for event in page.events:
            if event.kind == "result":
                found = event.data
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert found is not None, deps.registry.get_job(job.job_id)
    return found


@pytest.fixture
async def sandbox(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="wbs-") as folder:
        root = Path(folder)
        for name in ("state", "build", "harness", "clones", "seed", "protected"):
            (root/name).mkdir()
        (root / "build/romfs").mkdir()
        (root / "build/image.bin").write_bytes(b"synthetic")
        (root / "build/hookshot").write_text("#!/bin/sh\nexit 99\n")
        (root / "build/hookshot").chmod(0o700)
        (root / "seed/save").mkdir()
        (root / "seed/save/account.sav").write_bytes(b"synthetic account")
        for slot in (0, 1):
            (root / f"seed/save/slot_{slot:02}").mkdir()
            (root / f"seed/save/slot_{slot:02}/progress.sav").write_bytes(b"synthetic progress")
        rule = {"lanes": ["gpu", "intel"], "classes": ["ordinary", "whole"]}
        (root / "state/policy.json").write_text(json.dumps({"day": rule, "night": rule}))
        fake_script = Path(__file__).parent / "fakes/session_process.py"
        for name, mode in (("gate", "gate"), ("harness/hs-run.sh", "wrapper")):
            script = root/name
            script.write_text(f"#!{sys.executable}\nimport os, sys\nos.environ['SESSION_FIXTURE']={str(root)!r}\nos.execv({sys.executable!r}, [{sys.executable!r}, '-I', {str(fake_script)!r}, {mode!r}, *sys.argv[1:]])\n")
            script.chmod(0o700)
        (root / "gate-delay").write_text("0")
        (root / "quit-mode").write_text("reply")
        cat = {"builds": {"synthetic": {"path": str(root/"build"), "reviewed": True, "owned": True}},
            "saves": {"seed": {"path": str(root/"seed")}},
            "profiles": {"ordinary": {"debug": True}, "nondebug": {}}, "clone_root": str(root/"clones")}
        catpath = root / "catalogue.json"
        catpath.write_text(json.dumps(cat))
        config = Config(state_root=root/"state", read_roots=(root,), write_roots=(root,), protected_roots=(root/"protected",),
            harness_dir=root/"harness", gpu_gate=root/"gate", dependency_python=Path(sys.executable), profile="operate",
            session_catalogue=catpath)
        deps = Deps(config)
        monkeypatch.setattr(tool_registry, "GROUPS", ("status", "session"))
        service = SessionService(deps)
        monkeypatch.setattr(tools, "SessionService", lambda _: service)
        async with Client(build_server(deps=deps)) as client:
            yield root, deps, service, client


async def attach(root, deps, service, *, debug=True):
    run = deps.registry.create_run(state=State.RUNNING)
    job = deps.registry.create_job("session_fixture")
    directory = root / run.run_id
    directory.mkdir()
    bridge = await FAKE.FakeBridge(directory/"control.sock").start()
    session = Session(run.run_id, job.job_id, directory, directory/"control.sock", SessionProfile(debug=debug), {})
    service.sessions[run.run_id] = session
    return run.run_id, bridge


@pytest.mark.parametrize("profile,extras", [("observe", ()), ("operate", ()), ("observe", ("session_input",))])
async def test_session_catalogue(config, monkeypatch, profile, extras):
    monkeypatch.setattr(tool_registry, "GROUPS", ("status", "session"))
    async with Client(build_server(replace(config, profile=profile, extra_tools=extras))) as client:
        actual = {tool.name: tool for tool in await client.list_tools()}
        required = {"session_launch": ["build_id", "profile_id", "wall_seconds", "idempotency_key"],
            "session_input": ["run_id", "steps", "units", "idempotency_key"],
            "session_wait": ["run_id", "predicate", "idempotency_key"],
            "session_options_set": ["run_id", "values"], "session_teleport": ["run_id", "destination_id", "idempotency_key"],
            "session_age": ["run_id", "age", "idempotency_key"], "save_clone": ["save_id", "idempotency_key"],
            "session_stop": ["run_id", "reason", "idempotency_key"]}
        for spec in tools.SPECS:
            selected = profile == "operate" or spec.safety.value == "READ" or spec.name in extras
            assert (spec.name in actual) is selected
            if not selected:
                continue
            tool = actual[spec.name]
            assert tool.annotations.read_only_hint == spec.annotations["readOnlyHint"]
            assert tool.annotations.destructive_hint == spec.annotations["destructiveHint"]
            assert tool.annotations.idempotent_hint == spec.annotations["idempotentHint"]
            assert tool.annotations.open_world_hint is False
            assert tool.input_schema.get("required", []) == required.get(spec.name, ["run_id"])


async def test_bridge_dispatch_bounds_and_uncertain_quit(tmp_path):
    fake = await FAKE.FakeBridge(tmp_path/"bridge.sock").start()
    bridge = await Bridge.connect(fake.path)
    try:
        fake.delay = 0.05
        slow = asyncio.create_task(bridge.request("sequence", steps=[{"at": 0, "frames": 8}]))
        fast = await bridge.request("status")
        assert fast["protocol"] == 1
        assert (await slow)["end"] > 0
        assert len(bridge.drain_events()) == 2
        fake.quit_mode = "silent"
        with pytest.raises(WorkbenchError) as err:
            await bridge.request("quit", timeout=0.02)
        assert err.value.envelope.code == "uncertain_outcome"
        assert [c["cmd"] for c in fake.commands].count("quit") == 1
        for _ in range(260):
            await bridge.request("status")
        assert len(bridge.events) == 256 and bridge.events_lost == 5  # includes the silent quit's event
        with pytest.raises(WorkbenchError):
            await bridge.request("mem_write")
    finally:
        await bridge.close()
        await fake.close()


@pytest.mark.parametrize("at,duration,want_at,want_frames", [(0.025, 25, 2, 2), (0, 1, 0, 1), (2, 200, 120, 12)])
def test_explicit_input_units(at, duration, want_at, want_frames):
    value = InputStep(kind="press", buttons=["A"], at=at, duration=duration).bridge_step("hos")
    assert value == {"at": want_at, "frames": want_frames, "buttons": ["A"]}
    assert InputStep(kind="press", buttons=["A"]).bridge_step("frames")["frames"] == 8
    with pytest.raises(ValueError):
        InputStep(kind="press", buttons=["A"], duration=1.5).bridge_step("frames")


async def test_owned_bridge_happy_paths(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    try:
        state = (await client.call_tool("session_state", {"run_id": run_id, "expected_map": "SyntheticZone"})).data
        assert state.controllable is True and state.age_ms is not None
        args = {"run_id": run_id, "steps": [{"kind": "press", "buttons": ["A"], "at": 0.025, "duration": 25}], "units": "hos", "idempotency_key": "input"}
        one = (await client.call_tool("session_input", args)).data
        two = (await client.call_tool("session_input", args)).data
        assert one.job_id == two.job_id
        assert (await finished(deps, one)).state == State.COMPLETED
        inp = result(deps, one)
        assert inp["effect_unverified"] and inp["end_frame"]-inp["start_frame"] == 4
        assert len([r for r in fake.commands if r["cmd"] == "sequence"]) == 1
        waiting = (await client.call_tool("session_wait", {"run_id": run_id, "predicate": {"kind": "controllable"}, "stable_frames": 2, "idempotency_key": "wait"})).data
        await finished(deps, waiting)
        assert result(deps, waiting)["matched"] is True
        shot_response = await client.call_tool("session_shot", {"run_id": run_id})
        shot = shot_response.data
        assert shot.artifact.bytes > 24 and shot.width == 1 and shot.overlay is False
        assert str(root) not in json.dumps(shot_response.structured_content)
        page = (await client.call_tool("session_options_get", {"run_id": run_id, "limit": 1})).data
        assert page.next_cursor == 1
        page2 = (await client.call_tool("session_options_get", {"run_id": run_id, "cursor": page.next_cursor})).data
        assert len(page2.options) == 2
        changed = (await client.call_tool("session_options_set", {"run_id": run_id, "values": {"text_speed": 3}, "restore_on_finish": True})).data
        assert changed.before[0].value == 1 and changed.after[0].value == 3
        assert changed.restart_required is False
        await service._restore(service.sessions[run_id])
        assert fake.options["text_speed"] == 1
        for name, data in (("session_teleport", {"destination_id": 1}), ("session_age", {"age": "adult"})):
            arg = {"run_id": run_id, "idempotency_key": name, **data}
            travel = (await client.call_tool(name, arg)).data
            assert (await client.call_tool(name, arg)).data.job_id == travel.job_id
            assert (await finished(deps, travel)).state == State.COMPLETED
            assert result(deps, travel)["arrived"] is True
        saves_response = await client.call_tool("session_saves_list", {"run_id": run_id, "limit": 2})
        saves = saves_response.data
        assert saves.next_cursor == 2 and saves.write_consistency == "not_guaranteed"
        assert "private/game" not in json.dumps(saves_response.structured_content) and "caption" not in json.dumps(saves_response.structured_content)
        assert len((await client.call_tool("session_saves_list", {"run_id": run_id, "cursor": 2})).data.slots) == 1
        release = (await client.call_tool("session_input", {"run_id": run_id, "steps": [{"kind": "release"}], "units": "frames", "idempotency_key": "release"})).data
        await finished(deps, release)
        assert result(deps, release)["completed"]
    finally:
        await fake.close()


async def test_freshness_timeout_and_cancellation_do_not_resend(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    try:
        fake.sampled = False
        sample = await service.state_impl(run_id)
        assert sample.fresh is None and sample.controllable is None and sample.age_ms is None
        wait = await service.wait_impl(run_id, Predicate(kind="controllable"), 0.1, 500, 0, "unsupported")
        await finished(deps, wait)
        assert result(deps, wait)["reason"] == "freshness_unsupported"
        fake.sampled = True
        fake.stale = True
        assert (await service.state_impl(run_id)).controllable is False
        wait = await service.wait_impl(run_id, Predicate(kind="controllable"), 0.05, 500, 0, "timeout")
        await finished(deps, wait)
        assert result(deps, wait)["reason"] == "timeout"
        fake.stale = False
        fake.event = True
        wait = await service.wait_impl(run_id, Predicate(kind="controllable"), 30, 500, 0, "cancelwait")
        await observed(lambda: wait.job_id in service.tasks)
        await deps.supervisor.cancel(wait.job_id)
        assert (await finished(deps, wait)).state == State.CANCELLED
        assert not [r for r in fake.commands if r["cmd"] in {"quit", "sequence", "release"}]
        fake.delay = 2
        action = await service.input_impl(run_id, [InputStep(kind="press", buttons=["A"])], "frames", 0.02, "uncertain")
        await finished(deps, action)
        assert deps.registry.get_job(action.job_id).error.code == "uncertain_outcome"
        assert len([r for r in fake.commands if r["cmd"] == "sequence"]) == 1
        assert (await service.input_impl(run_id, [InputStep(kind="press", buttons=["A"])], "frames", 0.02, "uncertain")).job_id == action.job_id
        action2 = await service.input_impl(run_id, [InputStep(kind="press", buttons=["A"])], "frames", 10, "cancelinput")
        await observed(lambda: len([r for r in fake.commands if r["cmd"] == "sequence"]) == 2)
        await deps.supervisor.cancel(action2.job_id)
        assert (await finished(deps, action2)).state == State.CANCELLED
        assert len([r for r in fake.commands if r["cmd"] == "release"]) == 1
        assert not any(r.get("all") for r in fake.commands)
    finally:
        await fake.close()


@pytest.mark.parametrize("tool,args,code", [
    ("session_options_set", {"values": {"microphone_device": 1}}, "refused_by_policy"),
    ("session_options_set", {"values": {"output_res": 4}}, "refused_by_policy"),
    ("session_options_set", {"values": {"Window mode": 1}}, "invalid_argument"),
    ("session_options_get", {"key": "missing"}, "not_found"),
    ("session_options_get", {"limit": 101}, "invalid_argument"),
    ("session_saves_list", {"cursor": -1}, "invalid_argument"),
    ("session_shot", {"label": "../../escape"}, "invalid_argument"),
    ("session_input", {"steps": [{"kind": "press", "buttons": ["UNRECOGNIZED"]}], "units": "frames", "idempotency_key": "bad"}, "invalid_argument"),
    ("session_state", {"fields": ["memory"]}, "invalid_argument"),
])
async def test_bounded_refusals_and_envelopes(sandbox, tool, args, code):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    try:
        response = await client.call_tool(tool, {"run_id": run_id, **args}, raise_on_error=False)
        assert response.is_error
        envelope = json.loads(response.content[0].text)
        assert envelope["code"] == code
        assert set(envelope) == {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}
        assert str(root) not in response.content[0].text and "secret" not in response.content[0].text
    finally:
        await fake.close()


async def test_owner_debug_and_prerequisites(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service, debug=False)
    try:
        for tool, extra in (("session_teleport", {"destination_id": 0}), ("session_age", {"age": "adult"})):
            response = await client.call_tool(tool, {"run_id": run_id, "idempotency_key": tool, **extra}, raise_on_error=False)
            assert json.loads(response.content[0].text)["code"] == "refused_by_policy"
        foreign = deps.registry.create_run(state=State.RUNNING)
        response = await client.call_tool("session_stop", {"run_id": foreign.run_id, "reason": "test", "idempotency_key": "foreign"}, raise_on_error=False)
        assert json.loads(response.content[0].text)["code"] == "refused_by_policy"
        service.sessions[run_id].profile = SessionProfile(debug=True)
        fake.age_supported = False
        age = await service.travel_impl(run_id, "age", "adult", 0.1, "unsupported-age")
        assert (await finished(deps, age)).error.code == "unavailable"
        assert len([r for r in fake.commands if r["cmd"] == "age"]) == 1
        (root/"state/policy.json").write_text("{}")
        with pytest.raises(WorkbenchError) as err:
            await service.launch_impl("synthetic", None, "ordinary", 30, "standard", "policy")
        assert err.value.envelope.code == "refused_by_policy"
    finally:
        await fake.close()


async def test_clone_manifest_dedup_and_source_unchanged(sandbox):
    root, deps, service, client = sandbox
    before = {p.relative_to(root/"seed").as_posix(): p.read_bytes() for p in (root/"seed").rglob("*") if p.is_file()}
    args = {"save_id": "seed", "slot": 1, "idempotency_key": "copy"}
    job = (await client.call_tool("save_clone", args)).data
    assert (await client.call_tool("save_clone", args)).data.job_id == job.job_id
    assert (await finished(deps, job)).state == State.COMPLETED
    copied = result(deps, job)
    assert copied["file_count"] == 2 and copied["validation_status"] == "copy_only_not_a_checkpoint"
    manifest = json.loads(deps.registry.artifact_path(copied["copy_hash_manifest"]["id"], deps.paths).read_text())
    assert len(manifest["files"]) == 2 and all(len(row["sha256"]) == 64 for row in manifest["files"])
    assert {p.relative_to(root/"seed").as_posix(): p.read_bytes() for p in (root/"seed").rglob("*") if p.is_file()} == before
    with pytest.raises(WorkbenchError):
        await clone_tree(deps, root/"seed", root/"clones"/copied["save_id"])
    (root/"seed/save/link").symlink_to(root/"protected")
    refusal = await client.call_tool("save_clone", {"save_id": "seed", "idempotency_key": "link"}, raise_on_error=False)
    assert json.loads(refusal.content[0].text)["code"] == "refused_by_policy"


@pytest.mark.parametrize("quit_mode,forced", [("reply", False), ("silent", True)])
async def test_launch_stop_and_owned_child_receipt(sandbox, quit_mode, forced):
    root, deps, service, client = sandbox
    (root/"quit-mode").write_text(quit_mode)
    args = {"build_id": "synthetic", "profile_id": "ordinary", "wall_seconds": 30, "save_id": "seed", "idempotency_key": "launch"}
    began = time.monotonic()
    launch = (await client.call_tool("session_launch", args)).data
    assert time.monotonic()-began < 2
    assert (await client.call_tool("session_launch", args)).data.job_id == launch.job_id
    await observed(lambda: (root/"pids.json").exists())
    run_id = deps.registry.get_job(launch.job_id).run_ids[0]
    await observed(lambda: deps.registry.get_run(run_id).state == State.RUNNING)
    pids = json.loads((root/"pids.json").read_text())
    stop = (await client.call_tool("session_stop", {"run_id": run_id, "reason": "synthetic stop", "idempotency_key": "stop"})).data
    assert (await finished(deps, stop)).state == State.COMPLETED
    stopped = result(deps, stop)
    assert stopped["process_exited"] and stopped["forced"] is forced
    assert stopped["save_drain"] == "unsupported" and stopped["artifacts_complete"]
    await finished(deps, launch)
    assert all(identity(pid) is None for pid in pids.values())
    run = deps.registry.get_run(run_id)
    assert any(deps.registry.artifact_path(a, deps.paths).name == "hookshot_log.txt" for a in run.artifacts)
    assert run.exit == 0 and run.timing_valid is False
    assert any(e.kind == "bridge_ready" for e in deps.registry.events(launch.job_id).events)


async def test_one_waiter_admission_recheck_and_cancellation(sandbox):
    root, deps, service, client = sandbox
    (root/"gate-delay").write_text("0.5")
    launch = await service.launch_impl("synthetic", None, "ordinary", 30, "standard", "queued")
    with pytest.raises(WorkbenchError) as err:
        await service.launch_impl("synthetic", None, "ordinary", 30, "standard", "second")
    assert err.value.envelope.code == "busy"
    (root/"state/policy.json").write_text("{}")
    assert (await finished(deps, launch)).state in {State.FAILED, State.INTERRUPTED}
    assert not (root/"pids.json").exists()
    run_id = deps.registry.get_job(launch.job_id).run_ids[0]
    assert (service.sessions[run_id].root/"admission-refused.json").exists()


async def test_cancel_launch_ends_worker_and_child(sandbox):
    root, deps, service, client = sandbox
    launch = await service.launch_impl("synthetic", None, "ordinary", 30, "standard", "cancel-launch")
    await observed(lambda: (root/"pids.json").exists())
    pids = json.loads((root/"pids.json").read_text())
    await deps.supervisor.cancel(launch.job_id)
    await finished(deps, launch)
    assert all(identity(pid) is None for pid in pids.values())
    process_job = service.sessions[deps.registry.get_job(launch.job_id).run_ids[0]].process_job
    receipt = deps.registry.receipt(process_job)
    assert receipt.process_exited and receipt.descendants_exited


async def test_unconfigured_sessions_remain_lazy(config, monkeypatch):
    monkeypatch.setattr(tool_registry, "GROUPS", ("status", "session"))
    async with Client(build_server(replace(config, profile="operate"))) as client:
        launch = await client.call_tool("session_launch", {"build_id": "absent", "profile_id": "absent", "wall_seconds": 1, "idempotency_key": "missing"}, raise_on_error=False)
        assert json.loads(launch.content[0].text)["code"] == "unavailable"
        clone = await client.call_tool("save_clone", {"save_id": "absent", "idempotency_key": "missing"}, raise_on_error=False)
        assert json.loads(clone.content[0].text)["code"] == "unavailable"


async def test_registered_paths_and_missing_wrapper(sandbox):
    root, deps, service, client = sandbox
    path = root / "catalogue.json"
    original = json.loads(path.read_text())
    altered = json.loads(path.read_text())
    altered["saves"]["seed"]["path"] = str(root/"protected")
    path.write_text(json.dumps(altered))
    with pytest.raises(WorkbenchError) as err:
        await service.clone_impl("seed", None, "protected-save")
    assert err.value.envelope.code == "refused_by_policy"
    altered = json.loads(json.dumps(original))
    altered["builds"]["synthetic"]["path"] = str(root/"protected")
    path.write_text(json.dumps(altered))
    with pytest.raises(WorkbenchError) as err:
        await service.launch_impl("synthetic", None, "ordinary", 1, "none", "protected-build")
    assert err.value.envelope.code == "refused_by_policy"
    path.write_text(json.dumps(original))
    (root/"harness/hs-run.sh").chmod(0o600)
    with pytest.raises(WorkbenchError) as err:
        await service.launch_impl("synthetic", None, "ordinary", 1, "none", "missing-wrapper")
    assert err.value.envelope.code == "unavailable"


async def test_clone_cancellation_and_registered_clone_reuse(sandbox):
    root, deps, service, client = sandbox
    first = await service.clone_impl("seed", None, "first-copy")
    await finished(deps, first)
    clone_id = result(deps, first)["save_id"]
    second = await service.clone_impl(clone_id, None, "clone-copy")
    await finished(deps, second)
    assert result(deps, second)["file_count"] == 3
    # Enough bounded chunks to observe cooperative cancellation without a
    # detached copy thread continuing to write after a cancelled receipt.
    with (root/"seed/save/large.sav").open("wb") as out:
        out.truncate(64*1024*1024)
    copying = await service.clone_impl("seed", None, "cancel-copy")
    await observed(lambda: copying.job_id in service.tasks)
    await deps.supervisor.cancel(copying.job_id)
    terminal = await finished(deps, copying)
    assert terminal.state == State.CANCELLED
    assert (await service.clone_impl("seed", None, "cancel-copy")).job_id == copying.job_id
    files = {p: p.stat().st_size for p in (root/"clones").rglob("*") if p.is_file()}
    await asyncio.sleep(0)
    assert files == {p: p.stat().st_size for p in (root/"clones").rglob("*") if p.is_file()}


async def test_wall_bound_without_cooperative_wrapper(sandbox):
    root, deps, service, client = sandbox
    began = time.monotonic()
    job = await service.launch_impl("synthetic", None, "ordinary", 1, "none", "wall")
    await finished(deps, job)
    assert time.monotonic()-began < 8
    pids = json.loads((root/"pids.json").read_text())
    assert all(identity(pid) is None for pid in pids.values())
    assert deps.registry.get_run(deps.registry.get_job(job.job_id).run_ids[0]).exit == 0


async def test_queue_cancel_does_not_start_game(sandbox):
    root, deps, service, client = sandbox
    (root/"gate-delay").write_text("10")
    job = await service.launch_impl("synthetic", None, "ordinary", 30, "none", "cancel-queued")
    run_id = deps.registry.get_job(job.job_id).run_ids[0]
    await observed(lambda: service.sessions[run_id].process_job is not None)
    other_service = SessionService(deps)
    with pytest.raises(WorkbenchError) as err:
        await other_service.launch_impl("synthetic", None, "ordinary", 30, "none", "other-waiter")
    assert err.value.envelope.code == "busy"
    await deps.supervisor.cancel(job.job_id)
    await finished(deps, job)
    assert not (root/"pids.json").exists()
    assert deps.registry.receipt(service.sessions[run_id].process_job).descendants_exited


async def test_restoration_respects_intervening_changes(sandbox):
    root, deps, service, client = sandbox
    run_id, fake = await attach(root, deps, service)
    try:
        await service.options_set_impl(run_id, {"text_speed": 2}, True)
        fake.options["text_speed"] = 4
        await service._restore(service.sessions[run_id])
        assert fake.options["text_speed"] == 4
        fake.event = True
        job = await service.travel_impl(run_id, "teleport", 1, 30, "cancel-travel")
        await observed(lambda: any(c["cmd"] == "teleport" and "to" in c for c in fake.commands))
        await deps.supervisor.cancel(job.job_id)
        assert (await finished(deps, job)).state == State.CANCELLED
        assert len([c for c in fake.commands if c["cmd"] == "teleport" and "to" in c]) == 1
        assert not any(c["cmd"] == "quit" for c in fake.commands)
    finally:
        await fake.close()
