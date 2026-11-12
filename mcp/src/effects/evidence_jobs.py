"""Submit approved CPU operations through the configured idle gate and core guardian."""
import hashlib
import json
from pathlib import Path
import sys
from src.models import TERMINAL
from src.effects.evidence_common import backend, configured, write_new

HOST_PATHS = ("state_root", "harness_dir", "checkout", "dependency_python", "gpu_gate", "intel_gate",
              "build_gate", "idle_gate", "panels_script", "journal_reader")
HOST_ROOTS = ("read_roots", "write_roots", "protected_roots")


def host_payload(config):
    return {**{key: str(value) if (value := getattr(config, key, None)) is not None else None for key in HOST_PATHS},
            **{key: [str(path) for path in getattr(config, key)] for key in HOST_ROOTS},
            "profile": config.profile, "extra_tools": list(config.extra_tools)}


async def submit_evidence_job(deps, operation, request, idempotency_key, wall_seconds):
    if operation not in {"evidence_analyze", "evidence_scan_frames", "evidence_panels"}:
        raise deps.error("refused_by_policy", operation)
    if not 1 <= wall_seconds <= 480:
        raise deps.error("invalid_argument", operation)
    gate = configured(deps, "idle_gate", operation)
    if operation == "evidence_analyze":
        backend(deps, "regress/run_case.py", operation)
    elif operation == "evidence_scan_frames":
        backend(deps, "scripts/framelog.py", operation, checkout=True)
        configured(deps, "dependency_python", operation)
    else:
        configured(deps, "panels_script", operation)
    request = {**request, "wall_seconds": wall_seconds}
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    deps.policy.authorize(lane="idle", resource_class="ordinary", wall_seconds=wall_seconds, plan_hash=digest)
    job = deps.registry.create_job(operation, request=request, idempotency_key=idempotency_key, total_steps=2)
    if job.state in TERMINAL or deps.registry.process(job.job_id)[0] is not None:
        return job
    parent = deps.config.state_root / "evidence-jobs"
    if not parent.exists():
        deps.paths.output(parent)
        parent.mkdir(mode=0o700, exist_ok=True)
    root = parent / job.job_id
    if root.exists():
        deps.paths.read(root)
        return deps.registry.get_job(job.job_id)
    deps.paths.output(root)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        deps.paths.read(root)
        return deps.registry.get_job(job.job_id)
    payload = {"operation": operation, "job_id": job.job_id, "request": request,
               "plan_hash": digest, "config": host_payload(deps.config)}
    spec = write_new(deps, root / "request.json", json.dumps(payload, sort_keys=True))
    # Input runs are references, not owned lifecycle children of an analysis Job.
    deps.registry.update_job(job.job_id, phase="waiting_for_idle",
                             progress_text="Waiting for the configured idle gate")
    # The interpreter belongs to this installed server. dependency_python is only for trusted upstream CLI backends.
    return await deps.supervisor.start(job.job_id,
        [str(gate), sys.executable, "-I", str(Path(__file__).with_name("evidence_worker.py")), str(spec)],
        cwd=root, wall_seconds=wall_seconds, queue_timeout_seconds=3600, queued=True)
