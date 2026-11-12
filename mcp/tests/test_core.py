import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path
import pytest
from fastmcp import Client
from src.config import Config
from src.errors import WorkbenchError
from src.main import build_server, configure_logging
from src.models import State, Receipt, Producer, utc_now
from src.policy import DENIED_TOOLS, Safety, ToolSpec, check_spec, PathGuards, Policy, child_environment


@pytest.mark.parametrize("profile,extra,cancel", [("observe", (), False), ("operate", (), True), ("observe", ("job_cancel",), True)])
async def test_catalogue(config, profile, extra, cancel):
    server = build_server(replace(config, profile=profile, extra_tools=extra))
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {"workbench_status", "queue_status", "jobs_list", "job_read", "job_wait"} | ({"job_cancel"} if cancel else set())
        assert not DENIED_TOOLS.intersection(tools)
        for name, tool in tools.items():
            assert tool.annotations.read_only_hint == (name != "job_cancel")
            assert tool.annotations.destructive_hint == (name == "job_cancel")
            assert tool.annotations.idempotent_hint is True
            assert tool.annotations.open_world_hint is False
            assert tool.input_schema.get("required", []) == (["job_id"] if name in {"job_read", "job_wait", "job_cancel"} else [])
        result = await client.call_tool("workbench_status")
        assert result.data["protected_player_active"] is None
        assert result.data["capabilities"]["harness"]["status"] == "unavailable"
        assert {r.uri for r in await client.list_resources()} == {"workbench://policy", "workbench://tools"}
        templates = {r.uri_template for r in await client.list_resource_templates()}
        assert templates == {"workbench://runs/{id}", "workbench://jobs/{id}/events{?cursor,limit}"}


def test_unknown_profile_and_extras(config):
    with pytest.raises(ValueError, match="Unknown extra tools: imaginary"):
        build_server(replace(config, extra_tools=("imaginary",)))
    with pytest.raises(ValueError, match="Unknown tool profile"):
        build_server(replace(config, profile="everything"))


@pytest.mark.parametrize("name", sorted(DENIED_TOOLS))
def test_refusal_table(name):
    with pytest.raises(ValueError, match="Refused tool registration"):
        check_spec(ToolSpec(name, Safety.READ, "Cannot be registered"))


async def test_error_envelopes(deps, monkeypatch):
    async with Client(build_server(deps=deps)) as client:
        invalid = await client.call_tool("jobs_list", {"limit": -1}, raise_on_error=False)
        envelope = json.loads(invalid.content[0].text)
        assert envelope["code"] == "invalid_argument"
        def broken(**kwargs):
            raise RuntimeError("secret backend text /private/local/file")
        monkeypatch.setattr(deps.registry, "list_jobs", broken)
        bad = await client.call_tool("jobs_list", raise_on_error=False)
        assert bad.is_error
        text = bad.content[0].text
        assert "secret" not in text and "/private" not in text and "RuntimeError" not in text
        error = json.loads(text)
        assert error["code"] == "backend_failed" and error["retryable"] is False
        assert set(error) == {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}


async def test_events_runs_and_wait(deps):
    async with Client(build_server(deps=deps)) as client:
        job = deps.registry.create_job("fixture")
        for n in range(4):
            deps.registry.append_event(job.job_id, "sample", {"number": n})
        result = await client.call_tool("job_read", {"job_id": job.job_id, "limit": 2})
        assert result.data.events.has_more
        page = json.loads((await client.read_resource(f"workbench://jobs/{job.job_id}/events?cursor=2&limit=2"))[0].text)
        assert len(page["events"]) == 2 and page["next_cursor"] == 4
        run = deps.registry.create_run()
        value = json.loads((await client.read_resource(f"workbench://runs/{run.run_id}"))[0].text)
        assert value["gate"]["class"] is None and value["timing_valid"] is None
        waiting = asyncio.create_task(client.call_tool("job_wait", {"job_id": job.job_id, "timeout_seconds": 30}))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not deps.registry.cancellation_requested(job.job_id)
        timed = await client.call_tool("job_wait", {"job_id": job.job_id, "timeout_seconds": 0.01})
        assert timed.data.timed_out and not timed.data.terminal
    assert deps.registry.owner is None


def test_logging_keeps_stdout_clean(capsys):
    configure_logging()
    logging.getLogger("hookshot_workbench").warning("fixture message")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "fixture message" in captured.err


def test_config_explicit_environment(tmp_path):
    config = Config.from_env({"HOME": str(tmp_path), "HOOKSHOT_WB_READ_ROOTS": json.dumps([str(tmp_path / "owned")])})
    assert config.state_root == tmp_path / ".local/state/hookshot-workbench"
    assert config.read_roots == (tmp_path / "owned",)
    assert tmp_path / ".config" in config.protected_roots
    with pytest.raises(ValueError):
        Config.from_env({"HOOKSHOT_WB_STATE_ROOT": "relative"})


def test_path_guards(config, tmp_path):
    owned = tmp_path / "owned"
    protected = tmp_path / "protected"
    owned.mkdir()
    protected.mkdir()
    guards = PathGuards(replace(config, read_roots=(owned,), write_roots=(owned,), protected_roots=(protected,)))
    (owned / "escape").symlink_to(protected, target_is_directory=True)
    (protected / "data").write_text("private")
    with pytest.raises(WorkbenchError):
        guards.read(owned / "escape/data")
    out = owned / "new.txt"
    import os
    os.close(guards.reserve_file(out))
    with pytest.raises(WorkbenchError):
        guards.output(out)


def test_policy_fails_closed_and_rereads(config):
    policy = Policy(config)
    args = dict(lane="gpu", resource_class="ordinary", wall_seconds=30, plan_hash="a" * 64)
    with pytest.raises(WorkbenchError):
        policy.authorize(**args)
    config.state_root.mkdir()
    rule = {"lanes": ["gpu"], "classes": ["ordinary"]}
    policy.path.write_text(json.dumps({"day": rule, "night": rule}))
    planned = policy.authorize(**args)
    policy.path.write_text("{}")
    assert planned.revision != policy.snapshot().revision
    with pytest.raises(WorkbenchError):
        policy.authorize(**args, stage="admission")
    policy.path.write_text('{"windowless":false}')
    assert policy.snapshot().source == "invalid_operator_file"


def test_child_environment_allowlist(tmp_path):
    env = child_environment(tmp_path, {"LANG": "C", "DISPLAY": ":0", "LD_PRELOAD": "bad", "SECRET": "bad"})
    assert env["LANG"] == "C" and "SECRET" not in env and "DISPLAY" not in env and "LD_PRELOAD" not in env
    assert env["HOME"] == str(tmp_path / "home")


async def test_registry_attempts_and_artifacts(deps, tmp_path):
    await deps.open()
    try:
        one = deps.registry.create_job("fixture", idempotency_key="same", request={"input": 1})
        assert deps.registry.create_job("fixture", idempotency_key="same", request={"input": 1}) == one
        with pytest.raises(WorkbenchError):
            deps.registry.create_job("fixture", idempotency_key="same", request={"input": 2})
        retry = deps.registry.create_job("fixture", idempotency_key="retry", request={"input": 1})
        assert retry.attempt_id != one.attempt_id
        receipt = Receipt(job_id=one.job_id, observed_at=utc_now(), exit_code=1, stop_reason="failed",
                          process_exited=True, descendants_exited=True, queue_seconds=0, held_seconds=1)
        deps.registry.finish(one.job_id, State.FAILED, receipt)
        with pytest.raises(WorkbenchError):
            deps.registry.update_job(one.job_id, progress_text="overwrite")
        sample = tmp_path / "sample.txt"
        sample.write_text("synthetic evidence")
        art = deps.registry.add_artifact(sample, guards=deps.paths, mime="text/plain", producer=Producer(tool="fixture"), complete=True)
        assert deps.registry.get_artifact(art.id) == art and art.clock is None
        assert str(tmp_path) not in art.model_dump_json()
    finally:
        await deps.close()
