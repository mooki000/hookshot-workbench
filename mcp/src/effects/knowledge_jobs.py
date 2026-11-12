"""Immutable job submissions through the shared owned-process supervisor."""
import inspect
import json
import os
from pathlib import Path
import sys
from src.models import State, TERMINAL, Producer, Receipt, utc_now
from src.models.knowledge import ThreadRef
from src.effects.knowledge_common import config, digest, read_bytes
from src.effects.knowledge_github import allowed_thread
from src.effects.knowledge_loop import preflight_scan


def stored_cursor(deps, artifact_id, threads):
    if artifact_id is None:
        return {}
    artifact = deps.registry.get_artifact(artifact_id)
    if artifact.producer.tool != "github_watch" or artifact.mime != "application/json" or not artifact.complete:
        raise deps.error("refused_by_policy", "github_watch")
    raw = read_bytes(deps, deps.registry.artifact_path(artifact_id, deps.paths), "github_watch")
    if digest(raw) != artifact.sha256:
        raise deps.error("busy", "github_watch")
    value = json.loads(raw)
    if value.get("threads") != threads or not isinstance(value.get("hashes"), dict):
        raise deps.error("invalid_argument", "github_watch")
    return value["hashes"]


def persist_result(deps, job_id, operation, value, sequence=0):
    path = deps.config.state_root / f"knowledge-{job_id}-result-{sequence}.json"
    fd = deps.paths.reserve_file(path)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    artifact = deps.registry.add_artifact(path, guards=deps.paths, mime="application/json",
        producer=Producer(tool=operation, version="1"), privacy="private", complete=True)
    previous = deps.registry.get_job(job_id).artifact_uris
    deps.registry.update_job(job_id, artifact_uris=[*previous, artifact.uri])
    deps.registry.append_event(job_id, "knowledge_result", {"artifact_uri": artifact.uri, "cursor": artifact.id,
        **({"snapshot_id": value["snapshot_id"]} if "snapshot_id" in value else {})})
    return artifact


async def start_job(deps, operation, request, idempotency_key, wall_seconds, preflight=None):
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        raise deps.error("invalid_argument", operation)
    # Do not silently strip the official reader's existing sign-in when integrating
    # against an older core. This is the one small supervisor adapter for the group.
    if "trusted_read_environment" not in inspect.signature(deps.supervisor.start).parameters:
        raise deps.error("unavailable", operation)
    job = deps.registry.create_job(operation, idempotency_key=idempotency_key, request=request)
    if job.state in TERMINAL or deps.registry.process(job.job_id)[0] is not None:
        return job
    payload_path = deps.config.state_root / f"knowledge-{job.job_id}-request.json"
    if payload_path.exists():
        if job.job_id not in deps.supervisor.tasks:
            deps.registry.update_job(job.job_id, phase="reconcile_submission", error=deps.error("uncertain_outcome", operation).envelope)
        return deps.registry.get_job(job.job_id)
    if preflight is not None:
        try:
            preflight()
        except Exception as error:
            envelope = getattr(error, "envelope", deps.error("backend_failed", operation).envelope)
            deps.registry.finish(job.job_id, State.FAILED, Receipt(job_id=job.job_id, observed_at=utc_now(),
                exit_code=None, stop_reason="preflight_refused", process_exited=True, descendants_exited=True,
                queue_seconds=None, held_seconds=None), error=envelope)
            raise
    settings = {key: getattr(deps.config, key) for key in ("state_root", "checkout", "dependency_python", "read_roots", "write_roots", "protected_roots")}
    settings = {key: [str(p) for p in value] if isinstance(value, tuple) else str(value) if value is not None else None for key, value in settings.items()}
    payload = {"job_id": job.job_id, "operation": operation, "request": request,
        "config": settings, "knowledge": config(deps).model_dump(mode="json")}
    fd = deps.paths.reserve_file(payload_path)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    deps.registry.append_event(job.job_id, "submission_reserved", {"operation": operation})
    return await deps.supervisor.start(job.job_id,
        [sys.executable, "-I", "-B", "-m", "src.effects.knowledge_worker", str(payload_path)],
        cwd=Path(__file__).resolve().parents[2], wall_seconds=wall_seconds,
        trusted_read_environment=True)


async def github_watch_impl(deps, threads, timeout_seconds, idempotency_key, prior_cursor=None):
    if not isinstance(threads, list) or not 1 <= len(threads) <= 20 or not 0.05 <= timeout_seconds <= 3600:
        raise deps.error("invalid_argument", "github_watch")
    refs = [ThreadRef.model_validate(value).model_dump() if not isinstance(value, ThreadRef) else value.model_dump() for value in threads]
    if len({(ref["repository"], ref["number"]) for ref in refs}) != len(refs):
        raise deps.error("invalid_argument", "github_watch")
    for ref in refs:
        allowed_thread(deps, ref["repository"], ref["number"], "github_watch")
    prior = stored_cursor(deps, prior_cursor, refs)
    request = {"threads": refs, "timeout_seconds": timeout_seconds, "prior_cursor": prior_cursor, "prior_hashes": prior}
    return await start_job(deps, "github_watch", request, idempotency_key, timeout_seconds + 5)


async def prloop_refresh_impl(deps, snapshot_id, idempotency_key):
    return await start_job(deps, "prloop_refresh", {"snapshot_id": snapshot_id}, idempotency_key, 900,
        preflight=lambda: preflight_scan(deps, snapshot_id))
