"""Explicit run pairing using the existing regression and nightly decision rules."""
from src.models.evidence import AnalysisResult
from src.effects.evidence_common import backend, load_backend, read_json, producer


def stored_analysis(deps, ident):
    art, data = read_json(deps, ident, "evidence_compare")
    if art.producer.tool != "evidence_analyze":
        raise deps.error("refused_by_policy", "evidence_compare")
    result = AnalysisResult.model_validate(data)
    if art.run_id != result.run_id or art.clock != result.clock:
        raise deps.error("refused_by_policy", "evidence_compare")
    return art, result


def _window_identity(result):
    window = result.window
    if window is None:
        return None
    if "first" in window:
        return {key: window[key] for key in ("first", "last")}
    return {"duration": window["end"] - window["start"]}


def evidence_compare_impl(deps, pairing):
    tool = "evidence_compare"
    shared_path = backend(deps, "regress/regress.py", tool)
    compare_path = backend(deps, "nightly/compare.py", tool)
    shared, compare = load_backend(shared_path), load_backend(compare_path)
    references = [item for pair in pairing.pairs for item in (pair.candidate, pair.reference)]
    if len({item.run_id for item in references}) != 4:
        raise deps.error("invalid_argument", tool)
    rows, rejected, records, runs = [], [], [], []
    identities = []
    for item in references:
        run = deps.registry.get_run(item.run_id)
        art, record = stored_analysis(deps, item.metrics_artifact_id)
        if record.run_id != item.run_id:
            raise deps.error("refused_by_policy", tool)
        reasons = list(record.rejections)
        if record.validity != "valid":
            reasons.append("analysis_rejected")
        if run.timing_valid is not True or run.gate.class_ != "exclusive":
            reasons.append("not_an_exclusive_valid_timing")
        if run.state.value != "completed":
            reasons.append("run_not_completed")
        if pairing.metric not in record.metrics or record.metrics.get(pairing.metric) is None:
            reasons.append("metric_absent")
        if not run.config_hash or not run.build.profile_id or not run.build.commit or not run.gate.request_id:
            reasons.append("provenance_incomplete")
        if record.context.get("commit") != run.build.commit or record.context.get("config_hash") != run.config_hash:
            reasons.append("stored_context_mismatch")
        identity = (record.analyzer_id, record.clock, record.units.get(pairing.metric),
            record.source, record.producer.version, run.config_hash, run.build.profile_id,
            run.gate.request_id, _window_identity(record), record.context.get("input_producer"))
        identities.append(identity)
        row = {"run_id": item.run_id, "metrics_artifact_id": art.id, "commit": run.build.commit,
               "value": record.metrics.get(pairing.metric), "clock": record.clock,
               "unit": record.units.get(pairing.metric), "validity": record.validity, "rejections": reasons}
        rows.append(row)
        if reasons:
            rejected.append({"run_id": item.run_id, "reasons": reasons})
        records.append(record)
        runs.append(run)
    if any(identity != identities[0] for identity in identities[1:]):
        raise deps.error("invalid_argument", tool)
    if runs[0].build.commit != runs[2].build.commit or runs[1].build.commit != runs[3].build.commit:
        raise deps.error("invalid_argument", tool)
    starts = [run.started_at for run in runs]
    if any(value is None for value in starts) or sorted(range(4), key=lambda i: starts[i]) not in ([0, 1, 2, 3], [1, 0, 3, 2]):
        rejected.append({"run_id": None, "reasons": ["pair_order_not_observed_as_alternating"]})
    scenario = {"kind": "timing", "repetitions": 2, "repeat_tolerance": 0.05,
                "measure": {"name": pairing.metric, "direction": pairing.direction}}
    backend_rows = []
    for i, record in enumerate(records):
        context = record.context
        backend_rows.append({"role": "candidate" if i % 2 == 0 else "reference", "result": {
            "valid": record.validity == "valid" and not rows[i]["rejections"],
            "disturbed": context.get("disturbed"), "kernel_errors": context.get("kernel_errors"),
            "load_before": context.get("load_before"), "load_after": context.get("load_after"),
            "others_cores_window": context.get("others_cores_window"), "others_cores_peak": context.get("others_cores_peak"),
            "value": record.metrics.get(pairing.metric), "summary": "Stored registered metric",
            "evidence": [references[i].metrics_artifact_id]}})
    decision = ({"state": "not-judged", "reason": "rejected input evidence"} if rejected else
                compare.compare_paired(backend_rows, scenario, shared))
    return {"rows": rows, "valid": [row["run_id"] for row in rows if not row["rejections"]],
        "rejected": rejected, "paired_ranges": {"reference": decision.get("previous"), "candidate": decision.get("new"),
            "gaps_worse_units": decision.get("paired_gaps_worse_units")},
        "repeat_spread": decision.get("combined_repeat_spread"), "verdict": decision["state"],
        "decision": decision, "not_measured": ["Display latency, continuous visual correctness, unrecorded settings or other hardware"],
        "provenance": {"pairing": pairing.model_dump(), "rules": [producer(tool, shared_path).model_dump(),
                                                                  producer(tool, compare_path).model_dump()]}}
