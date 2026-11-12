"""Approved retained-evidence adapters, reusing the regression parser's mathematics.

Thread CPU uses the regression profiler's sampled per-thread accounting, not stack
frequency or a Windows-only report. Units describe logical processor cores.
"""
import csv
import math
import re
from src.models.evidence import AnalysisArgs, AnalysisResult
from src.effects.evidence_common import (artifact_input, backend, load_backend, named_run_artifact, producer, read_json)
from src.effects.evidence_logs import run_faults_impl

ANALYZERS = {
    "present_frame_times": {"measures": "Intervals between game present returns, limiter included", "units": "ms, fps, frames",
        "clock": "game-present", "required_inputs": ["present trace", "frame window", "quiet accounting samples"],
        "backend": "regress/run_case.py:present_rows,statistics_of,quiet_measure"},
    "readback_waits": {"measures": "All-call and conditional readback waits; denominators retained", "units": "ms, calls, calls/frame",
        "clock": "host-monotonic", "required_inputs": ["readbacks CSV", "frame window", "monotonic window", "quiet accounting samples"],
        "backend": "regress/run_case.py:readback_metrics,quiet_measure"},
    "thread_cpu": {"measures": "Per-thread sampled CPU accounting, not stack-frequency estimates", "units": "cores, seconds",
        "clock": "host-monotonic", "required_inputs": ["accounting samples with thread ticks", "monotonic window"],
        "backend": "regress/run_case.py:thread_groups,quiet_measure"},
    "memory_lines": {"measures": "Observed renderer VRAM maximum and last allowance; no speed or leak claim", "units": "GB",
        "clock": "log-order", "required_inputs": ["complete retained log"],
        "backend": "regress/run_case.py:log_metrics"},
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _samples(deps, ident, run_id):
    art, data = read_json(deps, ident, "evidence_analyze", run_id=run_id)
    if art.clock != "host-monotonic":
        raise ValueError("Unknown accounting clock")
    if not isinstance(data, list) or not 2 <= len(data) <= 100000:
        raise ValueError("Invalid sample shape")
    if any(not isinstance(row, dict) or not _number(row.get("t")) for row in data):
        raise ValueError("Invalid accounting clock")
    if any(b["t"] <= a["t"] for a, b in zip(data, data[1:])):
        raise ValueError("Nonadvancing accounting clock")
    return data


def _context(deps, run):
    context = {"commit": run.build.commit, "source_stamp": run.build.source_stamp,
        "profile_id": run.build.profile_id, "config_hash": run.config_hash,
        "gate_request": run.gate.request_id, "gate_class": run.gate.class_,
        "load_before": None, "load_after": None, "others_cores_window": None,
        "others_cores_peak": None, "disturbed": None, "kernel_errors": None}
    retained = named_run_artifact(deps, run, {"evidence-context.json"}, "evidence_analyze", required=False)
    if retained:
        _, data = read_json(deps, retained[0].id, "evidence_analyze", run_id=run.run_id)
        for name in ("load_before", "load_after"):
            value = data.get(name)
            value = value[0] if isinstance(value, list) and value else value
            context[name] = value if _number(value) else None
        if type(data.get("disturbed")) is bool:
            context["disturbed"] = data["disturbed"]
    return context


def _present(deps, parser, path, args):
    rows = parser.present_rows(path)
    parsed = [(int(r[0]), int(r[1]), int(r[2])) for r in rows]
    if len(parsed) < 3 or any(t <= 0 or dt <= 0 for _, t, dt in parsed):
        raise ValueError("Missing present intervals")
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(parsed, parsed[1:])):
        raise ValueError("Nonadvancing present trace")
    first, last = args.frame_window.first, args.frame_window.last
    selected = [row for row in parsed if first <= row[0] <= last]
    if len(selected) < 3 or selected[0][0] != first or selected[-1][0] != last:
        raise ValueError("Incomplete present window")
    if any(b[0] != a[0] + 1 for a, b in zip(selected, selected[1:])):
        raise ValueError("Missing presents in window")
    values = [row[2] / 1e6 for row in selected[1:]]
    metrics = parser.statistics_of(values)
    units = {key: ("fps" if key.endswith("fps") else "ms" if key.endswith("_ms") else "frames") for key in metrics}
    return metrics, units, (selected[0][1] / 1e9, selected[-1][1] / 1e9)


def _readbacks(deps, parser, path, args):
    rows = list(csv.DictReader(parser.whole_lines(path).splitlines()))
    fields = {"frame_begin", "frame_end", "kind", "eligible", "waited", "wait_ms", "catchup_ms", "elapsed_ms", "fallback"}
    if not rows or any(fields - row.keys() for row in rows):
        raise ValueError("Missing readback instrumentation")
    if not any(args.frame_window.first <= int(row["frame_begin"]) <= args.frame_window.last for row in rows):
        raise ValueError("Readback window has no observations")
    metrics = parser.readback_metrics(path, args.frame_window.first, args.frame_window.last)
    if metrics.get("readbacks_per_frame", 0) == 0:
        # A missing population is not a measured zero conditional wait.
        for name in metrics:
            if name.startswith("readback_wait") and name.endswith("_ms"):
                metrics[name] = None
    units = {key: ("ms/frame" if key.endswith("_ms_per_frame") else "ms" if key.endswith("_ms") else
                   "calls/frame" if key.endswith("_per_frame") else "calls") for key in metrics}
    return metrics, units


def _threads(parser, samples, window):
    selected = [row for row in samples if window.start <= row["t"] <= window.end and "threads" in row]
    if len(selected) < 2 or selected[-1]["t"] - selected[0]["t"] < 0.8 * (window.end - window.start):
        raise ValueError("Incomplete thread window")
    first, last = selected[0], selected[-1]
    common = sorted(set(first["threads"]) & set(last["threads"]))
    if not common or len(common) > 256:
        raise ValueError("Invalid thread population")
    metrics = {"window_seconds": last["t"] - first["t"]}
    units = {"window_seconds": "seconds"}
    for tid in common:
        if not re.fullmatch(r"[0-9]{1,12}", str(tid)):
            raise ValueError("Invalid thread identity")
        before, after = first["threads"][tid], last["threads"][tid]
        if len(before) != 2 or len(after) != 2 or before[0] != after[0] or after[1] < before[1]:
            raise ValueError("Reused thread identity")
        # Use the existing accounting implementation for each stable thread, not new tick math.
        narrowed = [{**row, "threads": {tid: row["threads"][tid]}} for row in (first, last)]
        result = parser.thread_groups(narrowed, window.start, window.end)
        key = "thread_" + str(tid) + "_cores"
        metrics[key], units[key] = result["total"], "cores"
    return metrics, units


async def evidence_analyze_impl(deps, run_id, analyzer_id, arguments: AnalysisArgs):
    if analyzer_id not in ANALYZERS:
        raise deps.error("invalid_argument", "evidence_analyze")
    run = deps.registry.get_run(run_id)
    path = backend(deps, "regress/run_case.py", "evidence_analyze")
    context = _context(deps, run)
    inputs = [arguments.input_artifact_id]
    if arguments.samples_artifact_id and arguments.samples_artifact_id not in inputs:
        inputs.append(arguments.samples_artifact_id)
    result = dict(run_id=run_id, analyzer_id=analyzer_id, clock=ANALYZERS[analyzer_id]["clock"],
        window=arguments.frame_window.model_dump() if arguments.frame_window else
               arguments.time_window.model_dump() if arguments.time_window else None,
        producer=producer("evidence_analyze", path), inputs=inputs,
        source=ANALYZERS[analyzer_id]["backend"], context=context)
    rejections = []
    if context["disturbed"] is True:
        rejections.append("run_under_load")
    timing = analyzer_id != "memory_lines"
    if run.state.value != "completed":
        rejections.append("run_not_completed")
    if timing and run.timing_valid is not True:
        rejections.append("timing_not_validated_or_run_under_load")
    if analyzer_id in ("present_frame_times", "readback_waits") and arguments.frame_window is None:
        rejections.append("frame_window_required")
    if analyzer_id in ("readback_waits", "thread_cpu") and arguments.time_window is None:
        rejections.append("monotonic_window_required")
    if analyzer_id == "memory_lines" and (arguments.frame_window or arguments.time_window):
        rejections.append("memory_lines_has_only_full_log_coverage")
    sample_id = arguments.input_artifact_id if analyzer_id == "thread_cpu" else arguments.samples_artifact_id
    if timing and sample_id is None:
        rejections.append("quiet_accounting_samples_required")
    # Required trace absence/incompleteness is a rejected observation, not a fabricated zero.
    art = deps.registry.get_artifact(arguments.input_artifact_id)
    context["input_producer"] = art.producer.model_dump()
    if not art.complete:
        rejections.append("input_incomplete")
    expected_clock = {"present_frame_times": "game-present", "readback_waits": "host-monotonic",
                      "thread_cpu": "host-monotonic", "memory_lines": None}[analyzer_id]
    if expected_clock and art.clock != expected_clock:
        rejections.append("input_clock_mismatch_or_unknown")
    mimes = {"application/json"} if analyzer_id == "thread_cpu" else {"text/plain"} if analyzer_id == "memory_lines" else {"text/csv"}
    _, input_path = artifact_input(deps, art.id, "evidence_analyze", run_id=run_id, mimes=mimes, complete=False)
    if sample_id:
        sample_art, _ = artifact_input(deps, sample_id, "evidence_analyze", run_id=run_id,
                                      mimes={"application/json"}, complete=False)
        if not sample_art.complete:
            rejections.append("accounting_samples_incomplete")
        if sample_art.clock != "host-monotonic":
            rejections.append("accounting_clock_unknown")
    if rejections:
        return AnalysisResult(**result, validity="rejected", rejections=rejections)
    parser = load_backend(path)
    try:
        samples = _samples(deps, sample_id, run_id) if sample_id else None
        if analyzer_id == "present_frame_times":
            metrics, units, window = _present(deps, parser, input_path, arguments)
        elif analyzer_id == "readback_waits":
            metrics, units = _readbacks(deps, parser, input_path, arguments)
            window = arguments.time_window.start, arguments.time_window.end
        elif analyzer_id == "thread_cpu":
            metrics, units = _threads(parser, samples, arguments.time_window)
            window = arguments.time_window.start, arguments.time_window.end
        else:
            text = parser.whole_lines(input_path)
            observed, _ = parser.log_metrics(text, 0, 0)
            metrics = {key: observed[key] for key in ("vram_gb_max", "vram_allowed_gb") if key in observed}
            units = {key: "GB" for key in metrics}
            window = None
            if not metrics:
                rejections.append("memory_signature_absent")
        if samples:
            window = window or (samples[0]["t"], samples[-1]["t"])
            quiet = parser.quiet_measure(samples, *window)
            if quiet is None or not all(_number(quiet.get(k)) for k in ("mean", "peak")):
                rejections.append("quiet_window_not_covered")
            else:
                context.update(others_cores_window=quiet["mean"], others_cores_peak=quiet["peak"],
                    disturbed=quiet["mean"] >= 3 or quiet["peak"] > 6)
                if context["disturbed"]:
                    rejections.append("run_under_load")
            context["load_before"] = samples[0].get("load") if _number(samples[0].get("load")) else None
            context["load_after"] = samples[-1].get("load") if _number(samples[-1].get("load")) else None
            if timing:
                result["window"] = {**result["window"], "monotonic_start": window[0], "monotonic_end": window[1]}
        faults = await run_faults_impl(deps, run_id)
        if faults["kernel"]["status"] == "complete":
            context["kernel_errors"] = faults["kernel"]["faults"] + faults["kernel"]["unknown"]
        if faults["stop_required"] is True:
            rejections.append("run_or_driver_fault")
        if any(value is not None and not _number(value) for value in metrics.values()):
            rejections.append("nonfinite_backend_metric")
    except (AssertionError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        metrics, units = {}, {}
        rejections.append("required_input_invalid_or_window_not_covered")
    return AnalysisResult(**result, metrics={} if rejections else metrics, units={} if rejections else units,
                          validity="rejected" if rejections else "valid", rejections=rejections)
