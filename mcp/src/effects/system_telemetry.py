"""Read registered proposed perf.csv observations without changing timing analyzers."""
import asyncio
import json
from typing import Literal
from pydantic import Field
from src.errors import WorkbenchError
from src.models import Record
from src.models.evidence import AnalysisArgs, AnalysisResult, FrameWindow, TimeWindow, RunId
from src.effects.evidence_common import artifact_input, backend, load_backend, producer, redact

PROPOSAL = "https://github.com/NotRexed/Hookshot/issues/2915"
MAX_INPUT = 64 * 1024 * 1024
MAX_OUTPUT = 128 * 1024


class SystemTelemetryResult(AnalysisResult):
    analyzer_id: Literal["system_telemetry"] = "system_telemetry"
    metrics: dict[str, float | int | None] = Field(default_factory=dict, max_length=369)
    units: dict[str, str] = Field(default_factory=dict, max_length=369)


class TelemetryObservation(Record):
    run_id: RunId
    available: bool
    message: str | None = None
    format_status: Literal["proposal"] = "proposal"
    proposal: Literal["https://github.com/NotRexed/Hookshot/issues/2915"] = PROPOSAL
    analysis: SystemTelemetryResult | None = None


def _bounded(value):
    if isinstance(value, dict):
        return {key: _bounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded(item) for item in value]
    if isinstance(value, str):
        return redact(value[:512])[0][:240]
    return value


def _clock(value):
    # These are explicit renderer-instrument labels, not a game-present fallback.
    return {"renderer-offscreen-submit": "headless-renderer", "swapchain-present": "swapchain"}.get(value, value)


def _named_csv(deps, run, name):
    found = []
    for ident in run.artifacts:
        art = deps.registry.get_artifact(ident)
        if art.run_id != run.run_id or art.mime != "text/csv":
            continue
        try:
            path = deps.registry.artifact_path(ident, deps.paths)
        except WorkbenchError as exc:
            if exc.envelope.code == "not_found":
                continue
            raise
        if path.name == name:
            found.append((art, path))
    if len(found) > 1:
        raise deps.error("refused_by_policy", "system_telemetry")
    return found[0] if found else None


def _analyze(deps, run, art, arguments):
    operation = "system_telemetry"
    parser_path = backend(deps, "perf/hs_perf.py", operation)
    parser = load_backend(parser_path)
    art, path = artifact_input(deps, art.id, operation, run_id=run.run_id,
        mimes={"text/csv"}, complete=False, max_bytes=MAX_INPUT, verify=art.complete)
    result = dict(run_id=run.run_id, analyzer_id="system_telemetry", producer=producer(operation, parser_path),
        source="perf/hs_perf.py:read_perf,summarize", inputs=[art.id], clock=None, window=None,
        context=dict(commit=run.build.commit, config_hash=run.config_hash, profile_id=run.build.profile_id,
            input_producer=art.producer.model_dump(), input_complete=art.complete,
            run_state=run.state.value, timing_valid=False, speed_verdict="refused", notice=parser.NOTICE,
            format_status="proposal", proposal=PROPOSAL))
    if arguments.frame_window and arguments.time_window:
        return SystemTelemetryResult(**result, validity="rejected", rejections=["ambiguous_telemetry_window"])
    start = end = None
    if arguments.frame_window:
        start, end = f"frame:{arguments.frame_window.first}", f"frame:{arguments.frame_window.last}"
    elif arguments.time_window:
        start, end = f"monotonic:{arguments.time_window.start}", f"monotonic:{arguments.time_window.end}"
    try:
        cap = parser.read_perf(path, max_bytes=MAX_INPUT)
        result["clock"] = cap.clock
        if _clock(art.clock) != cap.clock or cap.clock not in parser.CLOCKS:
            return SystemTelemetryResult(**result, validity="rejected", rejections=["input_clock_mismatch_or_unknown"])
        frame_log = None
        retained = _named_csv(deps, run, "frames.csv")
        if retained:
            frame_art, frame_path = artifact_input(deps, retained[0].id, operation, run_id=run.run_id,
                mimes={"text/csv"}, complete=False, max_bytes=MAX_INPUT, verify=retained[0].complete)
            if _clock(frame_art.clock) != cap.clock:
                return SystemTelemetryResult(**result, validity="rejected", rejections=["frame_clock_mismatch_or_unknown"])
            frame_log = parser.read_frames(frame_path, max_bytes=MAX_INPUT)
            result["inputs"].append(frame_art.id)
        report = parser.summarize(cap, start, end, frame_log)
        result["window"] = report["window"]
        result["context"].update(capture_complete=report["complete"], completion=report["completion"],
            join=_bounded(report["join"]), summary_verification=_bounded(report["summary_verification"]),
            suspicions=_bounded(report["suspicions"]), issues=_bounded(report["issues"][:30]),
            issue_count=report["issue_count"], omitted_issues=max(0, report["issue_count"] - 30))
        errors = list(dict.fromkeys(item["code"] for item in report["issues"] if item["severity"] == "error"))
        if report["summary_verification"]["status"] == "mismatch":
            errors.append("summary_mismatch")
        if not report["window"]["samples"]:
            errors.append("no_samples_in_window")
        if report["window"]["missing_window_coordinate"]:
            errors.append("sample_window_coordinate_unavailable")
        metrics, units = {}, {}
        if not errors:
            for name, summary in report["metrics"].items():
                for statistic in ("count", "missing_count", "mean", "median", "p5", "p95", "min", "max"):
                    key = name + "." + statistic
                    metrics[key] = summary[statistic]
                    units[key] = "sample receipts" if statistic in {"count", "missing_count"} else summary["unit"]
        answer = SystemTelemetryResult(**result, metrics=metrics, units=units,
            validity="rejected" if errors else "valid", rejections=errors[:30])
        if len(json.dumps(answer.model_dump(), allow_nan=False).encode()) > MAX_OUTPUT:
            return SystemTelemetryResult(**{**result, "context": {"speed_verdict": "refused", "format_status": "proposal"}},
                validity="rejected", rejections=["telemetry_output_limit_exceeded"])
        return answer
    except parser.PerfError as exc:
        return SystemTelemetryResult(**result, validity="rejected", rejections=[exc.code])


async def observe(deps, run_id: str, frame_window: FrameWindow | None = None,
                  time_window: TimeWindow | None = None) -> TelemetryObservation:
    run = deps.registry.get_run(run_id)
    selected = _named_csv(deps, run, "perf.csv")
    if selected is None:
        return TelemetryObservation(run_id=run_id, available=False, message="no performance file in this run")
    arguments = AnalysisArgs(input_artifact_id=selected[0].id, frame_window=frame_window, time_window=time_window)
    result = await asyncio.to_thread(_analyze, deps, run, selected[0], arguments)
    return TelemetryObservation(run_id=run_id, available=True, analysis=result)
