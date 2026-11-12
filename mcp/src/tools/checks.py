"""Thin MCP registration for builds, source checks and read-only CI evidence."""
import asyncio
from typing import Annotated
from pydantic import Field
from src.models import Job
from src.models.checks import CIResult, ImportedCommit, Name, PlanResult, Sha, TestName
from src.policy import Safety, ToolSpec
from src.effects.checks import build_plan_impl, submit_impl
from src.effects.checks_ci import ci_read_impl

SPECS = (
    ToolSpec("build_plan", Safety.WRITE_LOCAL, "Retain a conservative pinned build plan and dry-run evidence; never build or request a gate.", idempotent=False, shell_route="Pinned build/donor planning and compiler dry-run inspection"),
    ToolSpec("build_start", Safety.RUN, "Start an operator-approved build plan as an owned gated Job; never prepare shaders, accept SDK terms or upgrade dependencies.", shell_route="Gated full/reused build wrappers and completion bookkeeping"),
    ToolSpec("checks_source", Safety.RUN, "Run the reviewed game-free suite or exact declared test names; optionally compare only failures with a reviewed base commit.", shell_route="Idle-gated source test runner and failing-name comparison"),
    ToolSpec("checks_windows_syntax", Safety.RUN, "Run the existing Windows-target fixture check; Windows runtime is always not_run.", shell_route="Windows-target fixture check wrapper (not Windows runtime)"),
    ToolSpec("checks_pr", Safety.WRITE_LOCAL, "Run the configured repository-bound pre-push check, including its fetch; never push or publish.", open_world=True, shell_route="Repository-bound pre-push check; whole imported-commit attestations only"),
    ToolSpec("ci_read", Safety.READ, "Read allowlisted PR or run checks and failing test names; compare with completed main runs without rerunning or modifying CI.", open_world=True, shell_route="CI reader plus failed-job/log and matching-main inspection"),
)

ArtifactId = Annotated[str, Field(pattern=r"^artifact_[0-9a-f]{32}$")]
RunId = Annotated[str, Field(pattern=r"^run_[0-9a-f]{32}$")]
Key = Annotated[str, Field(min_length=1, max_length=128)]
Names = Annotated[list[TestName], Field(max_length=100)]
Repository = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=160)]
Branch = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,199}$")]


def register(mcp, deps):
    @deps.tool(mcp, "build_plan")
    async def build_plan(repo_id: Name, commit: Sha, profile_id: Name, donor_id: RunId | None = None) -> PlanResult:
        return await asyncio.to_thread(build_plan_impl, deps, repo_id, commit, profile_id, donor_id)

    @deps.tool(mcp, "build_start")
    async def build_start(plan_id: ArtifactId, idempotency_key: Key) -> Job:
        return await submit_impl(deps, "build_start", {"plan_id": plan_id}, idempotency_key)

    @deps.tool(mcp, "checks_source")
    async def checks_source(repo_id: Name, commit: Sha, idempotency_key: Key, profile_id: Name = "default",
                            tests: Names = [], workers: Annotated[int, Field(ge=1, le=8)] = 8,
                            compare_with_base: Sha | None = None) -> Job:
        return await submit_impl(deps, "checks_source", dict(repo_id=repo_id, commit=commit, profile_id=profile_id,
            tests=tests, workers=workers, compare_with_base=compare_with_base), idempotency_key)

    @deps.tool(mcp, "checks_windows_syntax")
    async def checks_windows_syntax(repo_id: Name, commit: Sha, base: Sha, idempotency_key: Key,
                                   profile_id: Name = "default", fixtures: Names = []) -> Job:
        return await submit_impl(deps, "checks_windows_syntax", dict(repo_id=repo_id, commit=commit, base=base,
            profile_id=profile_id, fixtures=fixtures), idempotency_key)

    @deps.tool(mcp, "checks_pr")
    async def checks_pr(repo_id: Name, commit: Sha, branch: Branch, title_artifact: ArtifactId, idempotency_key: Key,
                        imported: Annotated[list[ImportedCommit], Field(max_length=30)] = [], profile_id: Name = "default") -> Job:
        return await submit_impl(deps, "checks_pr", dict(repo_id=repo_id, commit=commit, branch=branch,
            title_artifact=title_artifact, imported=[p.model_dump() for p in imported], profile_id=profile_id), idempotency_key)

    @deps.tool(mcp, "ci_read")
    async def ci_read(repository: Repository, pull_request: Annotated[int, Field(ge=1)] | None = None,
                      run_id: Annotated[int, Field(ge=1)] | None = None, cursor: Annotated[int, Field(ge=0)] = 0,
                      limit: Annotated[int, Field(ge=1, le=100)] = 10) -> CIResult:
        return await asyncio.to_thread(ci_read_impl, deps, repository, pull_request, run_id, cursor, limit)
