import asyncio
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

import pytest
from fastmcp import Client
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError
from src.main import build_server
from src.models import Producer, Receipt, State, utc_now
from src.models.evidence import AnalysisArgs, BundleRequest, FrameWindow, Pairing, PanelImage, PanelLayout, TimeWindow
from src.effects.evidence_analysis import ANALYZERS, evidence_analyze_impl
from src.effects.evidence_bundle import evidence_bundle_impl
from src.effects.evidence_common import output_directory, store_json
from src.effects.evidence_compare import evidence_compare_impl
from src.effects.evidence_images import evidence_panels_impl, evidence_scan_frames_impl
from src.effects.evidence_logs import run_faults_impl, run_logs_impl
from src.effects.evidence_signatures import classify_kernel
from src.registry.process import identity
from src.tools import registry
from src.tools.evidence import SPECS


@dataclass(frozen=True)
class EvidenceConfig(Config):
    idle_gate: Path | None = None
    panels_script: Path | None = None
    journal_reader: Path | None = None


@pytest.fixture
def host(tmp_path, monkeypatch):
    fake = Path(__file__).parent / "fakes"
    harness, checkout = tmp_path / "harness", tmp_path / "checkout"
    for path in (harness / "regress", harness / "nightly", checkout / "scripts", tmp_path / "panels"):
        path.mkdir(parents=True)
    for source, destination in (
        ("evidence_run_case.py", harness / "regress/run_case.py"),
        ("evidence_regress.py", harness / "regress/regress.py"),
        ("evidence_compare.py", harness / "nightly/compare.py"),
        ("evidence_framelog.py", checkout / "scripts/framelog.py"),
        ("evidence_panels.py", tmp_path / "panels/panels.py"),
    ):
        destination.write_bytes((fake / source).read_bytes())
        destination.chmod(0o700)
    gate = tmp_path / "idle-gate"
    gate.write_text('#!/bin/sh\nexec "$@"\n')
    gate.chmod(0o700)
    config = EvidenceConfig(state_root=tmp_path / "state", harness_dir=harness, checkout=checkout,
        idle_gate=gate, panels_script=tmp_path / "panels/panels.py", dependency_python=Path(sys.executable).resolve(),
        read_roots=(tmp_path, Path(sys.executable).resolve().parent), write_roots=(tmp_path,),
        protected_roots=(tmp_path / "protected",), profile="operate")
    config.state_root.mkdir()
    rule = {"lanes": ["idle"], "classes": ["ordinary"], "max_hold_seconds": 480}
    (config.state_root / "policy.json").write_text(json.dumps({"day": rule, "night": rule}))
    monkeypatch.setattr(registry, "GROUPS", ("status", "evidence"))
    return config


@pytest.fixture
async def edeps(host):
    deps = Deps(host)
    await deps.open()
    try:
        yield deps
    finally:
        await deps.close()


def add(deps, path, raw, mime, run_id=None, clock=None, complete=True, tool="fixture_capture"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.encode() if isinstance(raw, str) else raw)
    return deps.registry.add_artifact(path, guards=deps.paths, mime=mime, producer=Producer(tool=tool, version="1"),
                                      run_id=run_id, clock=clock, complete=complete)


def make_run(deps, *, value=10, ordinal=0, commit="a" * 40, timing=True, other=1, kernel="", log=None, terminal=True):
    run = deps.registry.create_run(state=State.RUNNING, timing_valid=timing,
        build={"commit": commit, "source_stamp": "synthetic", "binary_hashes": {"fixture": "0" * 64}, "profile_id": "fixture"},
        config_hash="f" * 64, gate={"class": "exclusive" if timing else "ordinary", "request_id": "fixture-hold"},
        started_at=f"2026-01-01T00:{ordinal:02d}:00+00:00")
    root = deps.config.state_root / run.run_id
    log_art = add(deps, root / "hookshot_log.txt", log if log is not None else "fixture log\nVRAM 2 of 8 GB\n",
                  "text/plain", run.run_id)
    kernel_art = add(deps, root / "kernel.txt", kernel, "text/plain", run.run_id)
    trace = "frame,return_ns,interval_ns\n" + "".join(f"{i+1},{int(100e9+i*value*1e6)},{int(value*1e6)}\n" for i in range(1001))
    present = add(deps, root / "present.csv", trace, "text/csv", run.run_id, "game-present")
    samples = [{"t": 100 + i * value / 10, "load": 1 + i / 100, "other": other, "busy": i * 200, "game": i * 100,
                "threads": {"1": ["worker", i * 100], "2": ["helper", i * 50]}} for i in range(11)]
    sample = add(deps, root / "samples.json", json.dumps(samples), "application/json", run.run_id, "host-monotonic")
    readbacks = add(deps, root / "readbacks.csv", "frame_begin,kind,eligible,waited,wait_ms,catchup_ms,elapsed_ms,frame_end,fallback\n"
        "1,invalidate,1,1,2,1,3,1,0\n2,invalidate,1,1,4,1,5,2,0\n", "text/csv", run.run_id, "host-monotonic")
    run = deps.registry.update_run(run.run_id, artifacts=[a.id for a in (log_art, kernel_art, present, sample, readbacks)],
        state=State.COMPLETED if terminal else State.RUNNING,
        ended_at=f"2026-01-01T00:{ordinal:02d}:30+00:00" if terminal else None, exit=124 if terminal else None)
    return run, {"log": log_art, "kernel": kernel_art, "present": present, "samples": sample, "readbacks": readbacks}, root


def analysis_args(arts, analyzer="present_frame_times"):
    if analyzer == "memory_lines":
        return AnalysisArgs(input_artifact_id=arts["log"].id)
    if analyzer == "thread_cpu":
        return AnalysisArgs(input_artifact_id=arts["samples"].id, time_window=TimeWindow(start=100, end=110))
    return AnalysisArgs(input_artifact_id=arts["present" if analyzer == "present_frame_times" else "readbacks"].id,
        frame_window=FrameWindow(first=1, last=1001), samples_artifact_id=arts["samples"].id,
        time_window=TimeWindow(start=100, end=110) if analyzer == "readback_waits" else None)


async def observed(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Observed state deadline expired")
        await asyncio.sleep(.02)


async def job_result(deps, job_id):
    await observed(lambda: deps.registry.receipt(job_id) is not None)
    job = deps.registry.get_job(job_id)
    assert job.state == State.COMPLETED, (job, deps.registry.events(job_id).model_dump())
    assert job.artifact_uris
    ident = job.artifact_uris[0].rsplit("/", 1)[-1]
    return job, json.loads(deps.registry.artifact_path(ident, deps.paths).read_bytes())


@pytest.mark.parametrize("profile,extra", [("observe", ()), ("operate", ()), ("observe", ("evidence_panels",))])
async def test_evidence_catalogue(host, profile, extra):
    async with Client(build_server(replace(host, profile=profile, extra_tools=extra))) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        expected = {spec.name for spec in SPECS if spec.safety.value == "READ" or profile == "operate"} | set(extra)
        assert set(tools) & {spec.name for spec in SPECS} == expected
        required = {"run_logs": ["run_id"], "run_faults": ["run_id"],
            "evidence_analyze": ["run_id", "analyzer_id", "arguments", "idempotency_key"],
            "evidence_scan_frames": ["capture_artifact_id", "idempotency_key"],
            "evidence_panels": ["images", "label", "idempotency_key"],
            "evidence_compare": ["pairing"], "evidence_bundle": ["request"]}
        for spec in SPECS:
            if spec.name in tools:
                tool = tools[spec.name]
                assert tool.input_schema["required"] == required[spec.name]
                assert tool.annotations.read_only_hint == (spec.safety.value == "READ")
                assert tool.annotations.idempotent_hint == spec.idempotent
                assert tool.annotations.destructive_hint is False
                assert tool.annotations.open_world_hint is False
        if "evidence_analyze" in tools:
            assert tools["evidence_analyze"].input_schema["properties"]["analyzer_id"]["enum"] == list(ANALYZERS)
            catalogue = json.loads((await client.read_resource("workbench://evidence/analyzers"))[0].text)
            assert catalogue == ANALYZERS
        else:
            assert "workbench://evidence/analyzers" not in {str(resource.uri) for resource in await client.list_resources()}


async def test_logs_bounds_cursors_filters_and_partial(edeps):
    run, arts, root = make_run(edeps, log="alpha\nFATAL example\npassword=hidden /private/location\nlast fragment", terminal=False)
    page = run_logs_impl(edeps, run.run_id, limit=1)
    assert page.lines == ["alpha"] and page.next_cursor and not page.complete
    page2 = run_logs_impl(edeps, run.run_id, cursor=page.next_cursor, limit=2)
    assert page2.lines == ["FATAL example", "password=[redacted] [local-path]"] and page2.redactions == 2
    with (root / "hookshot_log.txt").open("a") as stream:
        stream.write(" finished\n")
    page3 = run_logs_impl(edeps, run.run_id, cursor=page2.next_cursor)
    assert page3.lines == ["last fragment finished"]
    assert run_logs_impl(edeps, run.run_id, last_lines=2).lines == ["password=[redacted] [local-path]", "last fragment finished"]
    assert run_logs_impl(edeps, run.run_id, signature="fatal").lines == ["FATAL example"]
    with pytest.raises(WorkbenchError):
        run_logs_impl(edeps, run.run_id, cursor=page.next_cursor, signature="memory")
    with pytest.raises(WorkbenchError):
        run_logs_impl(edeps, run.run_id, signature=".*")


async def test_log_huge_lines_and_bytes(edeps):
    run, _, _ = make_run(edeps, log="x" * 70000 + "\n" + "a" * 200 + "\n" + "b" * 200 + "\n")
    page = run_logs_impl(edeps, run.run_id, byte_cap=256)
    assert page.lines == ["a" * 200] and page.truncated and page.omitted_lines == 1
    next_page = run_logs_impl(edeps, run.run_id, cursor=page.next_cursor, byte_cap=256)
    assert next_page.lines == ["b" * 200] and next_page.complete


async def test_fault_classification_and_unknown_journal(edeps):
    run, _, _ = make_run(edeps, kernel="NVRM: memMap_IMPL: CPU mapping not supported for addressSpace: 0x1\nlast print repeated 4 times\nNVRM: Xid 1\n", log="FATAL example\nVK_ERROR_DEVICE_LOST\n")
    result = await run_faults_impl(edeps, run.run_id)
    assert result["fatal"] == 1 and result["device_lost"] == 1
    assert result["kernel"]["known"] == 5 and result["kernel"]["faults"] == 1
    assert result["kernel"]["repeats"] == 4 and result["stop_required"] is True
    other = edeps.registry.create_run(state=State.RUNNING)
    art = add(edeps, edeps.config.state_root / "other/hookshot_log.txt", "fine\n", "text/plain", other.run_id)
    other = edeps.registry.update_run(other.run_id, artifacts=[art.id], state=State.COMPLETED)
    result = await run_faults_impl(edeps, other.run_id)
    assert result["kernel"]["status"] == "unknown" and result["kernel"]["faults"] is None
    assert result["stop_required"] is None


def test_allocation_and_repeat_boundaries():
    triple = ["NVRM: NV_ERR_NO_MEMORY _memdescAllocInternal @ mem_desc.c",
              "NVRM: rmStatus @ system_mem.c", "NVRM: pRmApi->Alloc @ nv_gpu_ops.c"]
    assert classify_kernel(triple, False)["stop_required"] is False
    assert classify_kernel(triple * 3, False)["allocation_events"] == 3
    assert classify_kernel(triple * 4, False)["stop_required"] is True
    assert classify_kernel(triple, True)["stop_required"] is True
    assert classify_kernel(triple, None)["stop_required"] is None
    assert classify_kernel(["NVRM: unfamiliar"], False)["unknown"] == 1
    assert classify_kernel(["xe tile error"], False)["faults"] == 1
    assert classify_kernel(["last message repeated 2 times"], False)["unknown"] == 1


@pytest.mark.parametrize("analyzer", list(ANALYZERS))
async def test_analyzer_registry_happy_paths(edeps, analyzer):
    run, arts, _ = make_run(edeps)
    result = await evidence_analyze_impl(edeps, run.run_id, analyzer, analysis_args(arts, analyzer))
    assert result.validity == "valid" and result.metrics and result.producer.version.startswith("sha256:")
    if analyzer == "present_frame_times":
        assert result.metrics == {"mean_ms": 10, "frames": 1000, "fps": 100}
    elif analyzer == "thread_cpu":
        assert result.metrics["thread_1_cores"] == 1 and result.metrics["thread_2_cores"] == .5
    elif analyzer == "readback_waits":
        assert result.metrics["readback_wait_mean_ms"] == 3
    else:
        assert result.metrics["vram_gb_max"] == 2


@pytest.mark.parametrize("problem", ["ordinary", "load", "missing_samples", "clock", "incomplete", "bad_trace"])
async def test_analyzer_rejects_unmeasured_numbers(edeps, problem):
    run, arts, root = make_run(edeps, timing=problem != "ordinary", other=8 if problem == "load" else 1)
    args = analysis_args(arts)
    if problem == "missing_samples":
        args = args.model_copy(update={"samples_artifact_id": None})
    if problem in {"clock", "incomplete", "bad_trace"}:
        new = add(edeps, root / "alternate.csv", "invalid\n" if problem == "bad_trace" else (root / "present.csv").read_bytes(),
            "text/csv", run.run_id, "swapchain-present" if problem == "clock" else "game-present", complete=problem != "incomplete")
        args = args.model_copy(update={"input_artifact_id": new.id})
    result = await evidence_analyze_impl(edeps, run.run_id, "present_frame_times", args)
    assert result.validity == "rejected" and result.metrics == {} and result.rejections


async def test_analysis_job_via_client_is_idempotent(host):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        run, arts, _ = make_run(deps)
        args = {"run_id": run.run_id, "analyzer_id": "present_frame_times", "arguments": analysis_args(arts).model_dump(), "idempotency_key": "analysis-one"}
        started = await client.call_tool("evidence_analyze", args)
        repeated = await client.call_tool("evidence_analyze", args)
        assert started.data.job_id == repeated.data.job_id
        job, result = await job_result(deps, started.data.job_id)
        assert result["metrics"]["mean_ms"] == 10
        assert len([e for e in deps.registry.events(job.job_id).events if e.kind == "process_started"]) == 1
        assert {e.kind for e in deps.registry.events(job.job_id).events} >= {"progress", "admitted", "evidence_ready", "finished"}
        again = await client.call_tool("evidence_analyze", args)
        assert again.data.job_id == job.job_id
        changed = await client.call_tool("evidence_analyze", {**args, "wall_seconds": 12}, raise_on_error=False)
        assert json.loads(changed.content[0].text)["code"] == "invalid_argument"


def image_artifact(deps, name="input.png"):
    return add(deps, deps.config.state_root / name, b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 2, 2), "image/png")


def capture_artifact(deps, sequences=(1, 2, 3, 7, 8, 9)):
    raw = b"".join(struct.pack("<QII", (i + 1) * 10**9, seq, 0) + b"\0" * 16 for i, seq in enumerate(sequences))
    return add(deps, deps.config.state_root / "capture.bin", raw, "application/x-hookshot-framelog", clock="command-recording")


async def test_panels_and_scan_jobs_via_client(host):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        image = image_artifact(deps)
        original = deps.registry.artifact_path(image.id, deps.paths).read_bytes()
        args = {"images": [{"artifact_id": image.id, "caption": "1. Synthetic panel"}], "label": "Synthetic", "idempotency_key": "panel"}
        job = (await client.call_tool("evidence_panels", args)).data
        _, panel = await job_result(deps, job.job_id)
        assert panel["inspection"] == "pending" and panel["source_hashes"] == {image.id: image.sha256}
        assert deps.registry.artifact_path(image.id, deps.paths).read_bytes() == original
        assert (await client.call_tool("evidence_panels", args)).data.job_id == job.job_id
        capture = capture_artifact(deps)
        args = {"capture_artifact_id": capture.id, "idempotency_key": "scan"}
        job = (await client.call_tool("evidence_scan_frames", args)).data
        _, scan = await job_result(deps, job.job_id)
        assert scan["requires_visual_review"] is True and scan["missing_frames"] == 3
        assert scan["eligible_triplets"] == 2 and scan["events_total"] == 1
        assert scan["coverage"]["contiguous"] is False
        assert (await client.call_tool("evidence_scan_frames", args)).data.job_id == job.job_id


@pytest.mark.parametrize("operation", ["evidence_analyze", "evidence_scan_frames", "evidence_panels"])
async def test_cancellation_ends_worker_and_detached_child(host, operation):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        if operation == "evidence_analyze":
            run, arts, _ = make_run(deps)
            location = host.harness_dir / "regress"
            args = {"run_id": run.run_id, "analyzer_id": "present_frame_times", "arguments": analysis_args(arts).model_dump()}
        elif operation == "evidence_scan_frames":
            location = host.checkout / "scripts"
            args = {"capture_artifact_id": capture_artifact(deps).id}
        else:
            location = host.panels_script.parent
            args = {"images": [{"artifact_id": image_artifact(deps).id, "caption": "1. Synthetic"}], "label": "Synthetic"}
        (location / "hang").touch()
        args["idempotency_key"] = operation
        started = time.monotonic()
        job = (await client.call_tool(operation, args)).data
        assert time.monotonic() - started < 5
        await observed(lambda: (location / "pids.json").exists())
        pids = json.loads((location / "pids.json").read_bytes())
        duplicate = (await client.call_tool(operation, args)).data
        assert duplicate.job_id == job.job_id
        await client.call_tool("job_cancel", {"job_id": job.job_id})
        await observed(lambda: deps.registry.receipt(job.job_id) is not None)
        receipt = deps.registry.receipt(job.job_id)
        assert receipt.process_exited and receipt.descendants_exited
        assert all(identity(pid) is None for pid in pids)
        assert deps.registry.get_job(job.job_id).state == State.CANCELLED
        assert len([e for e in deps.registry.events(job.job_id).events if e.kind == "process_started"]) == 1


async def stored_pair(deps, values=(12, 10, 12.1, 10.1)):
    items = []
    for i, value in enumerate(values):
        run, arts, _ = make_run(deps, value=value, ordinal=i, commit=("b" if i % 2 == 0 else "a") * 40)
        result = await evidence_analyze_impl(deps, run.run_id, "present_frame_times", analysis_args(arts))
        assert result.validity == "valid"
        output = store_json(deps, output_directory(deps), "metrics.json", result.model_dump(), "evidence_analyze", run_id=run.run_id, clock=result.clock)
        items.append({"run_id": run.run_id, "metrics_artifact_id": output.id})
    return Pairing.model_validate({"pairs": [{"candidate": items[0], "reference": items[1]}, {"candidate": items[2], "reference": items[3]}], "metric": "mean_ms"})


@pytest.mark.parametrize("values,verdict", [((12, 10, 12.1, 10.1), "regression"), ((9, 10, 9.01, 10.01), "improvement"),
    ((10.1, 10, 10.3, 10.2), "within-repeat-spread"), ((12, 10, 13, 10.1), "not-judged")])
async def test_comparison_reuses_shared_pair_rules(edeps, values, verdict):
    pairing = await stored_pair(edeps, values)
    result = evidence_compare_impl(edeps, pairing)
    assert result["verdict"] == verdict
    if verdict == "regression":
        assert (edeps.config.harness_dir / "regress/paired-called.json").exists()
        assert result["repeat_spread"] == pytest.approx(.2)
    assert len(result["rows"]) == 4


async def test_comparison_rejects_duplicates_and_unlike_sources(edeps):
    pairing = await stored_pair(edeps)
    duplicate = pairing.model_copy(update={"pairs": [pairing.pairs[0], pairing.pairs[0]]})
    with pytest.raises(WorkbenchError):
        evidence_compare_impl(edeps, duplicate)
    item = pairing.pairs[0].candidate
    art = edeps.registry.get_artifact(item.metrics_artifact_id)
    data = json.loads(edeps.registry.artifact_path(art.id, edeps.paths).read_bytes())
    data["producer"]["version"] = "another-parser"
    alternate = store_json(edeps, output_directory(edeps), "changed.json", data, "evidence_analyze", run_id=item.run_id, clock=art.clock)
    changed = pairing.model_dump()
    changed["pairs"][0]["candidate"]["metrics_artifact_id"] = alternate.id
    with pytest.raises(WorkbenchError):
        evidence_compare_impl(edeps, Pairing.model_validate(changed))


async def test_bundle_generated_numbers_failures_and_private_draft(edeps):
    pairing = await stored_pair(edeps)
    items = [item for pair in pairing.pairs for item in (pair.candidate, pair.reference)]
    image = image_artifact(edeps)
    build_run = edeps.registry.create_run(state=State.COMPLETED, build={"commit": "b" * 40})
    build = add(edeps, edeps.config.state_root / "build.json", json.dumps({
        "commit": "b" * 40, "binary_hashes": {"fixture": "0" * 64},
        "completed_receipt": {"kind": "builder_done", "exit": 0}}),
        "application/json", run_id=build_run.run_id, tool="build_start")
    check = edeps.registry.create_job("checks_source")
    check_result = store_json(edeps, output_directory(edeps), "check.json", {
        "commit": "b" * 40, "coverage": "suite", "table_complete": True, "failed": 0, "exit": 0}, "checks_source")
    edeps.registry.update_job(check.job_id, artifact_uris=[check_result.uri])
    edeps.registry.finish(check.job_id, State.COMPLETED, Receipt(job_id=check.job_id, observed_at=utc_now(), exit_code=0,
        stop_reason="exited", process_exited=True, descendants_exited=True, queue_seconds=0, held_seconds=1))
    request = BundleRequest(base="a" * 40, head="b" * 40, topic="Synthetic", rule="performance",
        run_ids=[item.run_id for item in items], metrics_artifact_ids=[item.metrics_artifact_id for item in items],
        build_artifact_ids=[build.id], check_ids=[check.job_id], pictures=[{"artifact_id": image.id}], pairing=pairing,
        limitations=["Synthetic coverage only"])
    result = evidence_bundle_impl(edeps, request)
    assert result["public_candidate_artifacts"] == []
    assert "completed_build_receipt_for_head" not in result["missing_requirements"]
    assert "full_source_suite_on_exact_head" not in result["missing_requirements"]
    assert "windows_runtime_coverage_review" in result["missing_requirements"]
    manifest = json.loads(edeps.registry.artifact_path(result["manifest"].id, edeps.paths).read_bytes())
    assert manifest["comparison"]["verdict"] == "regression" and manifest["pictures"][0]["inspection"] == "pending"
    table = edeps.registry.artifact_path(result["table"].id, edeps.paths).read_text()
    assert "12.1" in table and "10.1" in table
    draft = edeps.registry.get_artifact(result["draft_uri"].rsplit("/", 1)[-1])
    assert draft.privacy == "private" and result["publication"] == "not_published"
    next_bundle = evidence_bundle_impl(edeps, request)
    assert next_bundle["bundle_id"] != result["bundle_id"]


async def test_read_tools_and_bundle_via_real_client(host):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        run, _, _ = make_run(deps)
        assert (await client.call_tool("run_logs", {"run_id": run.run_id})).data.lines
        assert (await client.call_tool("run_faults", {"run_id": run.run_id})).data["stop_required"] is False
        pairing = await stored_pair(deps)
        assert (await client.call_tool("evidence_compare", {"pairing": pairing.model_dump()})).data["verdict"] == "regression"
        result = await client.call_tool("evidence_bundle", {"request": {"base": "a" * 40, "head": "b" * 40, "topic": "Fixture"}})
        assert result.data["draft_uri"].startswith("workbench://artifacts/")


async def test_unavailable_policy_protected_and_errors(host):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        run, arts, root = make_run(deps)
        args = {"run_id": run.run_id, "analyzer_id": "present_frame_times", "arguments": analysis_args(arts).model_dump(), "idempotency_key": "refused"}
        (host.state_root / "policy.json").write_text("{}")
        refusal = await client.call_tool("evidence_analyze", args, raise_on_error=False)
        assert json.loads(refusal.content[0].text)["code"] == "refused_by_policy"
        for method, arguments in [("run_logs", {"run_id": run.run_id, "limit": 501}),
                                  ("run_logs", {"run_id": run.run_id, "signature": ".*"}),
                                  ("evidence_bundle", {"request": {"base": "a" * 40, "head": "b" * 40, "topic": "Safe", "metrics": {"fps": 999}}})]:
            result = await client.call_tool(method, arguments, raise_on_error=False)
            assert json.loads(result.content[0].text)["code"] == "invalid_argument"
        protected = host.protected_roots[0]
        protected.mkdir()
        secret = protected / "secret"
        secret.write_text("password=never-return-this")
        original = root / "hookshot_log.txt"
        original.unlink()
        original.symlink_to(secret)
        refusal = await client.call_tool("run_logs", {"run_id": run.run_id}, raise_on_error=False)
        text = refusal.content[0].text
        assert json.loads(text)["code"] == "refused_by_policy"
        assert str(host.state_root) not in text and "never-return" not in text
    unavailable = Deps(replace(host, idle_gate=None))
    async with Client(build_server(deps=unavailable)) as client:
        run, arts, _ = make_run(unavailable)
        result = await client.call_tool("evidence_analyze", {**args, "run_id": run.run_id, "arguments": analysis_args(arts).model_dump()}, raise_on_error=False)
        assert json.loads(result.content[0].text)["code"] == "unavailable"


async def test_backend_exception_envelope_without_leaks(host, monkeypatch):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        run, _, _ = make_run(deps)
        def broken(*args, **kwargs):
            raise RuntimeError("private backend /secret/path confidential")
        monkeypatch.setattr(deps.registry, "get_run", broken)
        result = await client.call_tool("run_faults", {"run_id": run.run_id}, raise_on_error=False)
        text = result.content[0].text
        assert "private" not in text and "confidential" not in text and "/secret" not in text
        assert set(json.loads(text)) == {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}


@pytest.mark.parametrize("failure", [False, True])
async def test_configured_journal_is_windowed_and_unknown_on_failure(host, failure):
    journal = host.state_root / "journal-reader"
    journal.write_text("#!/usr/bin/env python3\nimport sys\nassert '--since' in sys.argv and '--until' in sys.argv\n" +
        ("print('secret backend failure', file=sys.stderr)\nraise SystemExit(1)\n" if failure else
         "print('NVRM: memMap_IMPL: CPU mapping not supported for addressSpace: 0x1')\n"))
    journal.chmod(0o700)
    deps = Deps(replace(host, journal_reader=journal))
    await deps.open()
    try:
        run = deps.registry.create_run(state=State.RUNNING, started_at="2026-01-01T00:00:00+00:00")
        log = add(deps, host.state_root / "journal-run/hookshot_log.txt", "fine\n", "text/plain", run.run_id)
        run = deps.registry.update_run(run.run_id, state=State.COMPLETED, artifacts=[log.id],
            ended_at="2026-01-01T00:00:01+00:00")
        result = await run_faults_impl(deps, run.run_id)
        assert result["kernel"]["known"] == (None if failure else 1)
        assert result["kernel"]["status"] == ("unknown" if failure else "complete")
    finally:
        await deps.close()


async def test_changed_artifact_and_unavailable_backends_refuse(edeps):
    run, arts, root = make_run(edeps)
    (root / "present.csv").write_text("changed\n")
    with pytest.raises(WorkbenchError) as error:
        await evidence_analyze_impl(edeps, run.run_id, "present_frame_times", analysis_args(arts))
    assert error.value.envelope.code == "refused_by_policy"
    missing = Deps(replace(edeps.config, panels_script=None, harness_dir=None, checkout=None))
    missing.registry = edeps.registry
    with pytest.raises(WorkbenchError) as error:
        evidence_panels_impl(missing, [PanelImage(artifact_id=image_artifact(edeps).id, caption="1. Fixture")],
                             PanelLayout(), "Fixture", output_directory(edeps))
    assert error.value.envelope.code == "unavailable"
    with pytest.raises(WorkbenchError) as error:
        evidence_scan_frames_impl(missing, capture_artifact(edeps).id, None, "brightness-triplet-v1", output_directory(edeps))
    assert error.value.envelope.code == "unavailable"
    with pytest.raises(WorkbenchError) as error:
        evidence_compare_impl(missing, Pairing.model_validate({"pairs": [
            {"reference": {"run_id": run.run_id, "metrics_artifact_id": arts["present"].id},
             "candidate": {"run_id": run.run_id, "metrics_artifact_id": arts["present"].id}}] * 2, "metric": "mean_ms"}))
    assert error.value.envelope.code == "unavailable"


async def test_analysis_job_does_not_adopt_source_run(host):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        run, arts, _ = make_run(deps, terminal=False)
        job = (await client.call_tool("evidence_analyze", {"run_id": run.run_id, "analyzer_id": "present_frame_times",
            "arguments": analysis_args(arts).model_dump(), "idempotency_key": "active-source"})).data
        _, result = await job_result(deps, job.job_id)
        assert result["validity"] == "rejected" and "run_not_completed" in result["rejections"]
        assert deps.registry.get_run(run.run_id).state == State.RUNNING


@pytest.mark.parametrize("operation", ["evidence_panels", "evidence_scan_frames"])
async def test_image_jobs_policy_refusal(host, operation):
    deps = Deps(host)
    async with Client(build_server(deps=deps)) as client:
        args = ({"images": [{"artifact_id": image_artifact(deps).id, "caption": "1. Fixture"}], "label": "Fixture"}
                if operation == "evidence_panels" else {"capture_artifact_id": capture_artifact(deps).id})
        (host.state_root / "policy.json").write_text("{}")
        result = await client.call_tool(operation, {**args, "idempotency_key": "refused"}, raise_on_error=False)
        assert json.loads(result.content[0].text)["code"] == "refused_by_policy"


async def test_capture_rejects_partial_nonadvancing_and_unobserved_windows(edeps):
    capture = capture_artifact(edeps, (1, 2, 3))
    with pytest.raises(WorkbenchError) as error:
        evidence_scan_frames_impl(edeps, capture.id, FrameWindow(first=1, last=9),
            "brightness-triplet-v1", output_directory(edeps))
    assert error.value.envelope.code == "invalid_argument"
    malformed = add(edeps, edeps.config.state_root / "partial.bin", b"partial", "application/x-hookshot-framelog")
    with pytest.raises(WorkbenchError) as error:
        evidence_scan_frames_impl(edeps, malformed.id, None, "brightness-triplet-v1", output_directory(edeps))
    assert error.value.envelope.code == "invalid_argument"
    duplicate = capture_artifact(edeps, (1, 1, 2))
    with pytest.raises(WorkbenchError):
        evidence_scan_frames_impl(edeps, duplicate.id, None, "brightness-triplet-v1", output_directory(edeps))


async def test_output_roots_and_foreign_evidence_refuse(edeps):
    image = image_artifact(edeps)
    readonly = Deps(replace(edeps.config, write_roots=()))
    readonly.registry = edeps.registry
    with pytest.raises(WorkbenchError) as error:
        evidence_bundle_impl(readonly, BundleRequest(topic="Fixture", base="a" * 40, head="b" * 40))
    assert error.value.envelope.code == "refused_by_policy"
    run, arts, _ = make_run(edeps)
    other, _, _ = make_run(edeps)
    with pytest.raises(WorkbenchError) as error:
        await evidence_analyze_impl(edeps, other.run_id, "present_frame_times", analysis_args(arts))
    assert error.value.envelope.code == "refused_by_policy"


async def test_memory_keeps_observed_context_and_rejects_known_load(edeps):
    run, arts, root = make_run(edeps, timing=False, terminal=False)
    context = add(edeps, root / "evidence-context.json",
        json.dumps({"load_before": [1, 2, 3], "load_after": [2, 3, 4], "disturbed": False}),
        "application/json", run.run_id)
    run = edeps.registry.update_run(run.run_id, artifacts=[*run.artifacts, context.id], state=State.COMPLETED)
    result = await evidence_analyze_impl(edeps, run.run_id, "memory_lines", analysis_args(arts, "memory_lines"))
    assert result.validity == "valid" and result.context["load_before"] == 1 and result.context["load_after"] == 2
    loud, arts, _ = make_run(edeps, other=8)
    args = analysis_args(arts, "memory_lines").model_copy(update={"samples_artifact_id": arts["samples"].id})
    result = await evidence_analyze_impl(edeps, loud.run_id, "memory_lines", args)
    assert result.validity == "rejected" and result.metrics == {} and "run_under_load" in result.rejections


async def test_incomplete_required_samples_reject_analysis(edeps):
    run, arts, root = make_run(edeps)
    incomplete = add(edeps, root / "unfinished-samples.json", (root / "samples.json").read_bytes(),
        "application/json", run.run_id, "host-monotonic", complete=False)
    args = analysis_args(arts).model_copy(update={"samples_artifact_id": incomplete.id})
    result = await evidence_analyze_impl(edeps, run.run_id, "present_frame_times", args)
    assert result.validity == "rejected" and "accounting_samples_incomplete" in result.rejections


def test_unrelated_kernel_repeat_is_not_a_driver_fault():
    result = classify_kernel(["unrelated kernel status", "last message repeated 3 times"], False)
    assert result["unknown"] == 0 and result["repeats"] == 0 and result["stop_required"] is False
