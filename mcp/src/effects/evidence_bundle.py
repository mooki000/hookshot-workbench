"""Private evidence manifests and number-derived drafts; never upload or post."""
import re
from src.models import TERMINAL
from src.effects.evidence_common import (IMAGE_MIMES, artifact_input, output_directory,
    producer, read_json, redact, store_json, write_new)
from src.effects.evidence_compare import evidence_compare_impl, stored_analysis


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def evidence_bundle_impl(deps, request):
    tool = "evidence_bundle"
    missing = []
    runs = [deps.registry.get_run(ident) for ident in request.run_ids]
    run_ids = {run.run_id for run in runs}
    if len(run_ids) != len(runs):
        raise deps.error("invalid_argument", tool)
    builds = []
    for ident in request.build_artifact_ids:
        art, data = read_json(deps, ident, tool)
        if art.producer.tool != "build_start" or not isinstance(data, dict):
            raise deps.error("refused_by_policy", tool)
        build = data
        commit = build.get("commit")
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise deps.error("invalid_argument", tool)
        receipt = build.get("completed_receipt")
        build_run = deps.registry.get_run(art.run_id) if art.run_id else None
        completed = (isinstance(receipt, dict) and receipt.get("exit") == 0
                     and receipt.get("kind") in {"builder_done", "linked_outputs"}
                     and bool(build.get("binary_hashes")) and build_run is not None
                     and build_run.state.value == "completed" and build_run.build.commit == commit)
        builds.append({"artifact_id": ident, "sha256": art.sha256, "commit": commit,
            "source_stamp": redact(str(build.get("source_stamp")))[0] if build.get("source_stamp") else None,
            "complete": completed})
    if not any(row["commit"] == request.head and row["complete"] for row in builds):
        missing.append("completed_build_receipt_for_head")
    checks = []
    for ident in request.check_ids:
        job = deps.registry.get_job(ident)
        receipt = deps.registry.receipt(ident)
        events = deps.registry.events(ident, limit=1).events
        operation = events[0].data.get("operation") if events else None
        if operation not in {"checks_source", "checks_windows_syntax", "checks_pr"}:
            raise deps.error("refused_by_policy", tool)
        checks.append({"job_id": ident, "operation": operation, "state": job.state.value,
            "receipt": receipt.model_dump() if receipt else None, "artifacts": job.artifact_uris,
            "tested_head": None, "scope": None})
        for uri in job.artifact_uris[:16]:
            if not uri.startswith("workbench://artifacts/"):
                continue
            metadata = deps.registry.get_artifact(uri.rsplit("/", 1)[-1])
            if metadata.mime != "application/json":
                continue
            art, data = read_json(deps, uri.rsplit("/", 1)[-1], tool)
            if art.producer.tool == operation and isinstance(data, dict):
                if operation == "checks_source" and data.get("commit") == request.head:
                    checks[-1]["tested_head"] = request.head
                    if data.get("table_complete") is True and data.get("failed") == 0 and data.get("exit") == 0:
                        checks[-1]["scope"] = {"suite": "full", "named": "targeted"}.get(data.get("coverage"))
                elif art.producer.source_commit == request.head:
                    checks[-1]["tested_head"] = request.head
                    checks[-1]["scope"] = "syntax" if operation == "checks_windows_syntax" else None
    if not any(row["operation"] == "checks_source" and row["state"] == "completed" and row["tested_head"] == request.head
               and row["scope"] == "full" and row["receipt"] and row["receipt"]["exit_code"] == 0 for row in checks):
        missing.append("full_source_suite_on_exact_head")
    # A syntax check never claims Windows execution, and no current reader proves both-way causality or authorship.
    missing.extend(("targeted_test_before_and_after_proof", "windows_runtime_coverage_review", "author_credit_review"))
    rows = []
    for ident in request.metrics_artifact_ids:
        art, result = stored_analysis(deps, ident)
        if result.run_id not in run_ids:
            raise deps.error("invalid_argument", tool)
        rows.append({"run_id": result.run_id, "metrics_artifact_id": ident, "sha256": art.sha256,
            "metrics": result.metrics, "units": result.units, "clock": result.clock, "window": result.window,
            "validity": result.validity, "rejections": result.rejections, "context": result.context,
            "producer": result.producer.model_dump()})
    # Every selected attempt appears, even if it failed or has no numeric evidence.
    for run in runs:
        if not any(row["run_id"] == run.run_id for row in rows):
            rows.append({"run_id": run.run_id, "metrics_artifact_id": None, "sha256": None,
                "metrics": {}, "units": {}, "clock": None, "window": None, "validity": "rejected",
                "rejections": ["no_stored_analysis"], "context": {"commit": run.build.commit,
                    "config_hash": run.config_hash}, "producer": None})
    if request.rule != "source" and not any(run.state.value == "completed" and run.build.commit == request.head for run in runs):
        missing.append("runtime_evidence_on_exact_head")
    if any(run.state not in TERMINAL for run in runs):
        missing.append("unfinished_runs")
    if any(not run.build.commit or not run.build.source_stamp or not run.config_hash or not run.build.binary_hashes for run in runs):
        missing.append("run_build_settings_provenance")
    if request.rule != "source" and (not rows or any(row["context"].get("load_before") is None or row["context"].get("load_after") is None for row in rows)):
        missing.append("observed_environment_and_load")
    pictures = []
    for selected in request.pictures:
        art, _ = artifact_input(deps, selected.artifact_id, tool, mimes=IMAGE_MIMES,
                                max_bytes=16 * 1024 * 1024)
        if art.run_id is not None and art.run_id not in run_ids:
            raise deps.error("invalid_argument", tool)
        pictures.append({"artifact_id": art.id, "sha256": art.sha256, "inspection": selected.inspection,
            "inspection_note": redact(selected.inspection_note)[0] if selected.inspection_note else None,
            "inspection_source": "caller_attestation" if selected.inspection == "inspected" else None})
    if request.rule in {"visual", "runtime"} and (not pictures or any(row["inspection"] != "inspected" for row in pictures)):
        missing.append("inspected_labelled_pictures")
    comparison = evidence_compare_impl(deps, request.pairing) if request.pairing else None
    if request.pairing:
        paired = {item.run_id for pair in request.pairing.pairs for item in (pair.reference, pair.candidate)}
        if not paired <= run_ids:
            raise deps.error("invalid_argument", tool)
    if request.rule == "performance" and (not comparison or comparison["verdict"] == "not-judged"):
        missing.append("accepted_paired_performance_evidence")
    limitations = [redact(text)[0] for text in request.limitations]
    limitations += ["Private draft only; no upload, publication, claim or approval occurred.",
        "Picture inspection is the caller's explicit attestation, not a conclusion of image generation.",
        "The selected records do not prove all attempts were supplied; review the topic's attempt inventory."]
    table = ["| Run | Tested commit | Settings | Metric | Value | Unit | Clock | Validity |",
             "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        for name, value in row["metrics"].items() or [("none", None)]:
            table.append("| " + " | ".join(_cell(x) for x in (row["run_id"], row["context"].get("commit"),
                row["context"].get("config_hash"), name, value, row["units"].get(name), row["clock"],
                row["validity"] + (": " + ", ".join(row["rejections"]) if row["rejections"] else ""))) + " |")
    table_text = "\n".join(table)
    manifest = {"schema_version": 1, "topic": request.topic, "pull_request": request.pull_request,
        "base": request.base, "head": request.head, "rule": request.rule,
        "builds": builds, "runs": [run.model_dump(by_alias=True) for run in runs], "checks": checks,
        "pictures": pictures, "results": rows, "comparison": comparison,
        "missing_requirements": missing, "limitations": limitations,
        "public_candidate_artifacts": [row["artifact_id"] for row in pictures if row["inspection"] == "inspected"],
        "publication": "not_published"}
    root = output_directory(deps, "bundle")
    manifest_art = store_json(deps, root, "manifest.json", manifest, tool)
    table_path = write_new(deps, root / "results.txt", table_text)
    table_art = deps.registry.add_artifact(table_path, guards=deps.paths, mime="text/plain", producer=producer(tool), complete=True)
    draft = (f"Private evidence draft: {request.topic or 'Pull request'}\n\nBase: {request.base}\nHead: {request.head}\n\n"
        "What is measured: only the stored observations in the generated table.\n"
        "Not measured: anything absent from these records; syntax is not runtime proof.\n\n" + table_text +
        "\n\nMissing evidence / human review:\n" + "\n".join("- " + item for item in missing) +
        "\n\nLimitations:\n" + "\n".join("- " + item for item in limitations) + "\n")
    draft_path = write_new(deps, root / "draft.txt", draft)
    draft_art = deps.registry.add_artifact(draft_path, guards=deps.paths, mime="text/plain", producer=producer(tool), complete=True)
    return {"bundle_id": manifest_art.id, "manifest": manifest_art, "table": table_art, "draft_uri": draft_art.uri,
            "missing_requirements": missing, "public_candidate_artifacts": manifest["public_candidate_artifacts"],
            "publication": "not_published"}
