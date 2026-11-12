"""Proposed-file reader through real MCP registration and a fake parser backend."""
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import get_args
import pytest
from fastmcp import Client
from src.deps import Deps
from src.main import build_server
from src.models import Producer, State
from src.models.evidence import AnalyzerId, AnalysisArgs
from src.effects.evidence_analysis import ANALYZERS
from src.effects.evidence_compare import stored_analysis
from src.effects import system_telemetry as telemetry
from src.errors import WorkbenchError


class FakeParser:
    CLOCKS = {"headless-renderer", "swapchain"}
    NOTICE = "Synthetic observations; no speed verdict."
    class PerfError(ValueError):
        def __init__(self, code):
            self.code = code
    def __init__(self):
        self.calls = []
    def read_perf(self, path, *, max_bytes):
        content = path.read_text()
        if content.startswith("unsupported"):
            raise self.PerfError("unsupported_version")
        return SimpleNamespace(clock="headless-renderer", complete=not content.startswith("partial"))
    def read_frames(self, path, *, max_bytes):
        return path.read_text()
    def summarize(self, cap, start, end, frame_log):
        self.calls.append((start, end, frame_log))
        return {"window": {"samples": 2, "missing_window_coordinate": 0}, "complete": cap.complete,
            "completion": "complete" if cap.complete else "no final summary", "join": {"matched": 2 if frame_log else 0},
            "summary_verification": {"status": "cannot_tell"}, "suspicions": [], "issues": [], "issue_count": 0,
            "metrics": {"cpu_pct": {"count": 2, "missing_count": 0, "mean": 15, "median": 15,
                "p5": 10, "p95": 20, "min": 10, "max": 20, "unit": "%"},
                "commit_bytes": {"count": 0, "missing_count": 2, "mean": None, "median": None,
                "p5": None, "p95": None, "min": None, "max": None, "unit": "bytes"}}}


@pytest.fixture
def setup(config, tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    (harness / "perf").mkdir(parents=True)
    (harness / "perf/hs_perf.py").write_text("# synthetic trusted parser fixture\n")
    parser = FakeParser()
    monkeypatch.setattr(telemetry, "load_backend", lambda _: parser)
    deps = Deps(replace(config, harness_dir=harness))
    return deps, parser, tmp_path


def add(deps, root, run, name="perf.csv", content="sampled fixture", clock="renderer-offscreen-submit", complete=True):
    path = root / run.run_id / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)
    art = deps.registry.add_artifact(path, guards=deps.paths, mime="text/csv", producer=Producer(tool="fixture"),
                                    run_id=run.run_id, clock=clock, complete=complete)
    current = deps.registry.get_run(run.run_id)
    deps.registry.update_run(run.run_id, artifacts=[*current.artifacts, art.id])
    return art, path


async def test_missing_file_is_a_normal_observation_without_backend(config):
    deps = Deps(config)
    async with Client(build_server(deps=deps)) as client:
        run = deps.registry.create_run()
        result = await client.call_tool("system_telemetry", {"run_id": run.run_id})
        assert not result.is_error
        value = result.structured_content
        assert value["available"] is False and value["message"] == "no performance file in this run"
        assert value["analysis"] is None and value["format_status"] == "proposal"
        assert value["proposal"].endswith("/2915")


@pytest.mark.parametrize("partial", [False, True])
async def test_observations_preserve_unknowns_and_never_change_timing_rules(setup, partial):
    deps, parser, root = setup
    async with Client(build_server(deps=deps)) as client:
        run = deps.registry.create_run(state=State.RUNNING, timing_valid=False)
        add(deps, root, run, content="partial fixture" if partial else "sampled fixture", complete=not partial)
        value = (await client.call_tool("system_telemetry", {"run_id": run.run_id})).structured_content
        assert value["available"] is True and value["analysis"]["validity"] == "valid"
        analysis = value["analysis"]
        assert analysis["metrics"]["commit_bytes.mean"] is None
        assert analysis["context"]["speed_verdict"] == "refused" and analysis["context"]["timing_valid"] is False
        assert analysis["context"]["capture_complete"] is not partial
        assert deps.registry.get_run(run.run_id).state == State.RUNNING
        assert not deps.registry.list_jobs().jobs
        assert set(get_args(AnalyzerId)) == set(ANALYZERS) == {"present_frame_times", "readback_waits", "thread_cpu", "memory_lines"}
        assert "system_telemetry" not in get_args(AnalyzerId)
        path = root / "telemetry-result.txt"
        path.write_text(json.dumps(analysis))
        artifact = deps.registry.add_artifact(path, guards=deps.paths, mime="application/json",
            producer=Producer(tool="system_telemetry"), complete=True)
        with pytest.raises(WorkbenchError):
            stored_analysis(deps, artifact.id)


async def test_windows_clock_and_integrity_are_checked(setup):
    deps, parser, root = setup
    async with Client(build_server(deps=deps)) as client:
        run = deps.registry.create_run()
        art, path = add(deps, root, run)
        add(deps, root, run, name="frames.csv", content="frame,time,ms\n1,0,16\n")
        value = (await client.call_tool("system_telemetry", {"run_id": run.run_id,
            "frame_window": {"first": 1, "last": 2}})).structured_content
        assert len(value["analysis"]["inputs"]) == 2 and parser.calls[-1][:2] == ("frame:1", "frame:2")
        await client.call_tool("system_telemetry", {"run_id": run.run_id, "time_window": {"start": 3, "end": 4}})
        assert parser.calls[-1][:2] == ("monotonic:3.0", "monotonic:4.0")
        path.write_text("changed")
        failed = await client.call_tool("system_telemetry", {"run_id": run.run_id}, raise_on_error=False)
        assert json.loads(failed.content[0].text)["code"] == "refused_by_policy"


async def test_deleted_file_and_parser_version(setup):
    deps, parser, root = setup
    async with Client(build_server(deps=deps)) as client:
        run = deps.registry.create_run()
        _, path = add(deps, root, run, content="unsupported version")
        value = (await client.call_tool("system_telemetry", {"run_id": run.run_id})).structured_content
        assert value["analysis"]["rejections"] == ["unsupported_version"]
        path.unlink()
        value = (await client.call_tool("system_telemetry", {"run_id": run.run_id})).structured_content
        assert value["available"] is False and value["message"] == "no performance file in this run"
