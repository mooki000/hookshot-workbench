"""Private guardian child; the MCP surface never accepts this entrypoint or argv."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

# Isolated mode cannot find the package from a script directory. This is our own installed package, not input code.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.deps import Deps
from src.errors import WorkbenchError
from src.models.evidence import AnalysisArgs, FrameWindow, PanelImage, PanelLayout
from src.effects.evidence_common import store_json
from src.effects.evidence_jobs import HOST_PATHS, HOST_ROOTS
from src.effects.evidence_analysis import evidence_analyze_impl
from src.effects.evidence_images import evidence_panels_impl, evidence_scan_frames_impl


async def execute(payload, root):
    values = payload["config"]
    for key in HOST_PATHS:
        values[key] = Path(values[key]) if values[key] is not None else None
    for key in HOST_ROOTS:
        values[key] = tuple(Path(path) for path in values[key])
    deps = Deps(SimpleNamespace(**values))
    deps.registry.open()
    job_id, operation, request = payload["job_id"], payload["operation"], payload["request"]
    try:
        deps.policy.authorize(lane="idle", resource_class="ordinary", wall_seconds=request["wall_seconds"],
                              plan_hash=payload["plan_hash"], stage="admission")
        deps.supervisor.mark_admitted(job_id)
        deps.registry.update_job(job_id, phase="analyzing", completed_steps=1,
                                 progress_text="Reading approved retained evidence")
        if operation == "evidence_analyze":
            result = await evidence_analyze_impl(deps, request["run_id"], request["analyzer_id"],
                                                AnalysisArgs.model_validate(request["arguments"]))
            data, run_id, clock = result.model_dump(), result.run_id, result.clock
        elif operation == "evidence_scan_frames":
            window = FrameWindow.model_validate(request["frame_window"]) if request["frame_window"] else None
            data = evidence_scan_frames_impl(deps, request["capture_artifact_id"], window, request["detector"], root)
            capture = deps.registry.get_artifact(request["capture_artifact_id"])
            run_id, clock = capture.run_id, capture.clock
        elif operation == "evidence_panels":
            data = evidence_panels_impl(deps, [PanelImage.model_validate(x) for x in request["images"]],
                PanelLayout.model_validate(request["layout"]), request["label"], root)
            run_id, clock = None, None
        else:
            raise deps.error("refused_by_policy", "evidence_worker")
        art = store_json(deps, root, "result.json", data, operation, run_id=run_id, clock=clock)
        uris = [art.uri]
        if operation == "evidence_panels":
            uris.append(data["artifact"]["uri"])
        elif operation == "evidence_scan_frames":
            uris.extend(item["uri"] for item in data["largest_strips"])
        deps.registry.update_job(job_id, phase="results_retained", completed_steps=2, artifact_uris=uris,
                                 progress_text="Evidence retained; awaiting owned-process receipt")
        deps.registry.append_event(job_id, "evidence_ready", {"result_uri": art.uri})
        return 0
    except Exception as exc:
        error = exc if isinstance(exc, WorkbenchError) else deps.error("backend_failed", operation)
        deps.registry.update_job(job_id, error=error.envelope, progress_text="Evidence worker failed; inspect the existing attempt")
        deps.registry.append_event(job_id, "evidence_error", error.envelope.model_dump())
        return 1
    finally:
        deps.registry.close()


def main():
    with Path(sys.argv[1]).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        return 1
    return asyncio.run(execute(json.loads(raw), Path(sys.argv[1]).parent))


if __name__ == "__main__":
    raise SystemExit(main())
