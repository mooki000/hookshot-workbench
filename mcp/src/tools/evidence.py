"""Logs, faults, registered numeric adapters and private evidence packages."""
from typing import Annotated, Literal
from pydantic import Field
from src.models import Job
from src.models.evidence import (AnalysisArgs, AnalyzerId, ArtifactId, BundleRequest, FrameWindow,
    Key, Label, LogPage, Pairing, PanelImage, PanelLayout, RunId, Signature)
from src.policy import Safety, ToolSpec
from src.effects.evidence_analysis import ANALYZERS
from src.effects.evidence_bundle import evidence_bundle_impl
from src.effects.evidence_common import artifact_input
from src.effects.evidence_compare import evidence_compare_impl
from src.effects.evidence_jobs import submit_evidence_job
from src.effects.evidence_logs import run_faults_impl, run_logs_impl

SPECS = (
    ToolSpec("run_logs", Safety.READ, "Read bounded whole retained log lines by cursor or last N; filters are approved signatures, not regexes.", shell_route="Bounded retained-log tail and approved signature filtering"),
    ToolSpec("run_faults", Safety.READ, "Classify retained fatal/device/driver lines; unavailable journal coverage stays unknown, known refusals stay separate.", shell_route="Manual game/kernel fault classification with coverage accounting"),
    ToolSpec("evidence_analyze", Safety.RUN, "Start an idle-gated approved analyzer: present_frame_times, readback_waits, thread_cpu or memory_lines. Returns a Job; invalid evidence produces rejected metrics, never invented zeroes.", shell_route="Idle-gated retained frame/readback/thread/memory analysis"),
    ToolSpec("evidence_scan_frames", Safety.RUN, "Start an idle-gated retained pixel-capture scan. Only consecutive triplets are tested; visual review remains required.", shell_route="Retained framelog inspection and existing detector invocation"),
    ToolSpec("evidence_panels", Safety.WRITE_LOCAL, "Start an owned idle-gated labelled panel Job in new output. Source hashes are retained and inspection remains pending.", shell_route="Source-preserving panels helper and image provenance bookkeeping"),
    ToolSpec("evidence_compare", Safety.READ, "Compare exactly two explicit existing run pairs with shared quiet/repeat/paired rules; reject unlike clocks, settings and instrument sources.", shell_route="Existing nightly/regression paired comparison rules"),
    ToolSpec("evidence_bundle", Safety.WRITE_LOCAL, "Assemble private manifest/table/draft artifacts from registered evidence. No typed metric numbers, upload, post or public approval.", idempotent=False, shell_route="Hand-assembled evidence tables and private handoff drafts"),
)
Wall = Annotated[int, Field(ge=1, le=480)]


def register(mcp, deps):
    if "evidence_analyze" in deps.selected:
        @mcp.resource("workbench://evidence/analyzers", mime_type="application/json",
                      description="Approved analyzer IDs, measurements, units, clocks and required retained inputs; no probe.")
        def analyzer_catalogue() -> dict:
            return ANALYZERS

    @deps.tool(mcp, "run_logs")
    async def run_logs(run_id: RunId, cursor: Annotated[str, Field(max_length=512)] | None = None,
                       last_lines: Annotated[int, Field(ge=1, le=500)] | None = None,
                       byte_cap: Annotated[int, Field(ge=256, le=65536)] = 16384,
                       signature: Signature | None = None,
                       limit: Annotated[int, Field(ge=1, le=500)] = 100) -> LogPage:
        return run_logs_impl(deps, run_id, cursor, last_lines, byte_cap, signature, limit)

    @deps.tool(mcp, "run_faults")
    async def run_faults(run_id: RunId) -> dict:
        return await run_faults_impl(deps, run_id)

    @deps.tool(mcp, "evidence_analyze")
    async def evidence_analyze(run_id: RunId, analyzer_id: AnalyzerId, arguments: AnalysisArgs,
                               idempotency_key: Key, wall_seconds: Wall = 120) -> Job:
        deps.registry.get_run(run_id)
        artifact_input(deps, arguments.input_artifact_id, "evidence_analyze", run_id=run_id,
                       complete=False, verify=False)
        if arguments.samples_artifact_id:
            artifact_input(deps, arguments.samples_artifact_id, "evidence_analyze", run_id=run_id,
                           complete=False, verify=False)
        return await submit_evidence_job(deps, "evidence_analyze", {"run_id": run_id,
            "analyzer_id": analyzer_id, "arguments": arguments.model_dump()}, idempotency_key, wall_seconds)

    @deps.tool(mcp, "evidence_scan_frames")
    async def evidence_scan_frames(capture_artifact_id: ArtifactId, idempotency_key: Key,
            detector: Literal["brightness-triplet-v1"] = "brightness-triplet-v1",
            frame_window: FrameWindow | None = None, wall_seconds: Wall = 480) -> Job:
        artifact_input(deps, capture_artifact_id, "evidence_scan_frames", mimes={"application/x-hookshot-framelog"},
                       max_bytes=1024 * 1024 * 1024, verify=False)
        return await submit_evidence_job(deps, "evidence_scan_frames", {"capture_artifact_id": capture_artifact_id,
            "detector": detector, "frame_window": frame_window.model_dump() if frame_window else None},
            idempotency_key, wall_seconds)

    @deps.tool(mcp, "evidence_panels")
    async def evidence_panels(images: Annotated[list[PanelImage], Field(min_length=1, max_length=16)],
            label: Label, idempotency_key: Key, layout: PanelLayout = PanelLayout(),
            wall_seconds: Wall = 300) -> Job:
        for image in images:
            artifact_input(deps, image.artifact_id, "evidence_panels", mimes={"image/png", "image/jpeg"},
                           max_bytes=16 * 1024 * 1024, verify=False)
        return await submit_evidence_job(deps, "evidence_panels", {"images": [image.model_dump() for image in images],
            "label": label, "layout": layout.model_dump()}, idempotency_key, wall_seconds)

    @deps.tool(mcp, "evidence_compare")
    async def evidence_compare(pairing: Pairing) -> dict:
        return evidence_compare_impl(deps, pairing)

    @deps.tool(mcp, "evidence_bundle")
    async def evidence_bundle(request: BundleRequest) -> dict:
        return evidence_bundle_impl(deps, request)
