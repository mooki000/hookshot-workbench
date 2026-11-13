"""Registry observation without launching or probing optional backends."""
import asyncio
from importlib.metadata import version
from typing import Annotated, Literal
from pydantic import Field
from src.models import JobPage, JobView, WaitResult, TERMINAL, utc_now
from src.models.gates import QueueSnapshot
from src.models.status import CancelResult, WorkbenchStatus
from src.policy import Safety, ToolSpec

SPECS = (
    ToolSpec("workbench_status", Safety.READ, "Read configured capabilities, current policy and bounded active jobs; no connectivity probe.", shell_route="Manual policy, prerequisite, revision and active-job inspection"),
    ToolSpec("queue_status", Safety.READ, "Observe GPU, Intel and build locks and tickets without acquiring a lock or creating files; wait estimates stay unknown.", shell_route="Manual queue/holder files and procfs lock inspection"),
    ToolSpec("jobs_list", Safety.READ, "List this registry's jobs with a bounded cursor page.", shell_route="Ad-hoc background process and topic job lists"),
    ToolSpec("job_read", Safety.READ, "Read one owned job, bounded events and its final child receipt.", shell_route="Manual progress logs and completion-receipt lookup"),
    ToolSpec("job_wait", Safety.READ, "Wait for observed terminal state under a deadline; cancelling this wait never cancels work.", shell_route="Repeated bounded completion checks without cancelling work"),
    ToolSpec("job_cancel", Safety.WRITE_LOCAL, "Request cancellation of one registry-owned worker; preserve its output and receipt.", destructive=True, shell_route="Hand-managed scoped cancellation and child receipts"),
)

Limit = Annotated[int, Field(ge=1, le=100)]
Cursor = Annotated[int, Field(ge=0, le=9223372036854775807)]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]


def status(deps) -> dict:
    config = deps.config
    def available(path, kind):
        present = path is not None and (path.is_dir() if kind == "directory" else path.is_file())
        return {"status": "configured" if present else "unavailable",
                "reason": None if present else "not_configured_or_missing", "connectivity": None}
    capabilities = {name: available(path, kind) for name, path, kind in (
        ("harness", config.harness_dir, "directory"), ("trusted_checkout", config.checkout, "directory"),
        ("gpu_gate", config.gpu_gate, "file"), ("intel_gate", config.intel_gate, "file"),
        ("build_gate", config.build_gate, "file"), ("idle_gate", config.idle_gate, "file"),
        ("dependency_python", config.dependency_python, "file"), ("session_catalogue", config.session_catalogue, "file"),
        ("checks_catalogue", config.checks_catalogue, "file"), ("panels_script", config.panels_script, "file"),
        ("journal_reader", config.journal_reader, "file"))}
    capabilities["knowledge"] = {"status": "configured" if config.knowledge is not None else "unavailable",
        "reason": config.knowledge_unavailable_reason if config.knowledge is None else None, "connectivity": None}
    gate_summary = deps.gates.snapshot()
    return {"observed_at": utc_now(), "policy": deps.policy.snapshot().model_dump(),
            "capabilities": capabilities,
            "active_jobs": deps.registry.list_jobs(active_only=True).model_dump(),
            "protected_player_active": None,
            "gate_summary": gate_summary.model_dump(),
            "tool_revisions": {"server": version("ls-workbench-mcp"), "fastmcp": version("fastmcp"),
                               **{q.device + "_gate": q.source_sha256 for q in gate_summary.devices}},
            "tools": deps.tool_catalogue()}


async def wait_job(deps, job_id: str, timeout_seconds: float) -> WaitResult:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        job = deps.registry.get_job(job_id)
        terminal = job.state in TERMINAL
        remaining = deadline - asyncio.get_running_loop().time()
        if terminal or remaining <= 0:
            return WaitResult(job=job, terminal=terminal, timed_out=not terminal,
                receipt=deps.registry.receipt(job_id), observed_at=utc_now())
        await asyncio.sleep(min(0.1, remaining))


def register(mcp, deps):
    @deps.tool(mcp, "workbench_status")
    async def workbench_status() -> WorkbenchStatus:
        return WorkbenchStatus.model_validate(status(deps))

    @deps.tool(mcp, "queue_status")
    async def queue_status(device: Literal["gpu", "intel", "build"] | None = None,
                           job_id: JobId | None = None, cursor: Cursor = 0, limit: Limit = 50) -> QueueSnapshot:
        request_pid = None
        if job_id is not None:
            event = deps.registry.latest_event(job_id, "gate_request")
            if event is not None:
                request_pid = event.data.get("pid")
        return deps.gates.snapshot(device=device, request_pid=request_pid, cursor=cursor, limit=limit)

    @deps.tool(mcp, "jobs_list")
    async def jobs_list(cursor: Cursor = 0, limit: Limit = 25, active_only: bool = False) -> JobPage:
        return deps.registry.list_jobs(cursor=cursor, limit=limit, active_only=active_only)

    @deps.tool(mcp, "job_read")
    async def job_read(job_id: JobId, cursor: Cursor = 0, limit: Limit = 50) -> JobView:
        return JobView(job=deps.registry.get_job(job_id),
            events=deps.registry.events(job_id, cursor=cursor, limit=limit),
            receipt=deps.registry.receipt(job_id), observed_at=utc_now())

    @deps.tool(mcp, "job_wait")
    async def job_wait(job_id: JobId, timeout_seconds: Annotated[float, Field(ge=0, le=30)] = 10) -> WaitResult:
        return await wait_job(deps, job_id, timeout_seconds)

    @deps.tool(mcp, "job_cancel")
    async def job_cancel(job_id: JobId) -> CancelResult:
        return CancelResult.model_validate(await deps.supervisor.cancel(job_id))
