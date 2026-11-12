"""Thin typed knowledge registration; all backends also work without MCP."""
from typing import Annotated, Literal
from pydantic import Field
from src.errors import WorkbenchError
from src.models import Job
from src.models.knowledge import ThreadRef, ThreadSnapshot, ReaderResult, SearchResult
from src.policy import Safety, ToolSpec
from src.effects.knowledge_github import github_thread_read_impl
from src.effects.knowledge_jobs import github_watch_impl, prloop_refresh_impl
from src.effects.knowledge_loop import prloop_next_impl
from src.effects.knowledge_team import test_requests_read_impl, team_read_impl
from src.effects.knowledge_nightly import nightly_status_impl
from src.effects.knowledge_documents import knowledge_search_impl

SPECS = (
    ToolSpec("github_thread_read", Safety.READ, "Read bounded, untrusted issue/PR text with full pagination status, head and snapshot hash; GET only.", open_world=True),
    ToolSpec("github_watch", Safety.WRITE_LOCAL, "Watch allowlisted threads until a deadline as an owned Job; persist private cursors, never post.", open_world=True),
    ToolSpec("prloop_refresh", Safety.WRITE_LOCAL, "Scan an initialized loop snapshot as an owned Job under its coordinator lock; never claim or attest review.", open_world=True),
    ToolSpec("prloop_next", Safety.READ, "Read the installed loop's next action on existing state; never initialize configuration or execute the proposed action."),
    ToolSpec("test_requests_read", Safety.READ, "Read approved request-room cards only through an attested GET-only/no-save trusted CLI; stock unsafe first reads refuse.", open_world=True),
    ToolSpec("team_read", Safety.READ, "Read an allowlisted existing room through the trusted GET-only/no-save CLI; no joining, sign-in or settings access.", open_world=True),
    ToolSpec("nightly_status", Safety.READ, "Observe retained nightly state and read-only SQLite; never run rolling --check or construct its store."),
    ToolSpec("knowledge_search", Safety.READ, "Search titles, headings and first lines of explicitly registered skills/reports; bounded revision-bound cursors."),
)

Limit = Annotated[int, Field(ge=1, le=100)]
Cursor = Annotated[str, Field(max_length=256)]
SnapshotId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, Field(min_length=1, max_length=128)]


async def invoke(deps, operation, function, *args, **kwargs):
    try:
        return await function(deps, *args, **kwargs)
    except WorkbenchError:
        raise
    except Exception:
        raise deps.error("backend_failed", operation) from None


def invoke_sync(deps, operation, function, *args, **kwargs):
    try:
        return function(deps, *args, **kwargs)
    except WorkbenchError:
        raise
    except Exception:
        raise deps.error("backend_failed", operation) from None


def register(mcp, deps):
    @deps.tool(mcp, "github_thread_read")
    async def github_thread_read(repository: Annotated[str, Field(max_length=200)], number: Annotated[int, Field(ge=1, le=2147483647)],
                                 cursor: Cursor | None = None, limit: Limit = 25) -> ThreadSnapshot:
        return await invoke(deps, "github_thread_read", github_thread_read_impl, repository, number, cursor, limit)

    @deps.tool(mcp, "github_watch")
    async def github_watch(threads: Annotated[list[ThreadRef], Field(min_length=1, max_length=20)],
                           timeout_seconds: Annotated[float, Field(ge=0.05, le=3600)], idempotency_key: Key,
                           prior_cursor: Annotated[str, Field(pattern=r"^artifact_[0-9a-f]{32}$")] | None = None) -> Job:
        return await invoke(deps, "github_watch", github_watch_impl, threads, timeout_seconds, idempotency_key, prior_cursor)

    @deps.tool(mcp, "prloop_refresh")
    async def prloop_refresh(snapshot_id: SnapshotId, idempotency_key: Key) -> Job:
        return await invoke(deps, "prloop_refresh", prloop_refresh_impl, snapshot_id, idempotency_key)

    @deps.tool(mcp, "prloop_next")
    async def prloop_next(snapshot_id: SnapshotId | None = None, why: bool = False) -> dict:
        return invoke_sync(deps, "prloop_next", prloop_next_impl, snapshot_id, why)

    @deps.tool(mcp, "test_requests_read")
    async def test_requests_read(card_id: Annotated[str, Field(pattern=r"^T-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}$")] | None = None,
                                 status: Literal["open", "all"] = "open", mine: bool = False,
                                 cursor: Cursor | None = None, limit: Limit = 20) -> ReaderResult:
        return await invoke(deps, "test_requests_read", test_requests_read_impl, card_id, status, mine, cursor, limit)

    @deps.tool(mcp, "team_read")
    async def team_read(room: Annotated[str, Field(min_length=1, max_length=256)], limit: Limit = 20,
                        cursor: Cursor | None = None) -> ReaderResult:
        return await invoke(deps, "team_read", team_read_impl, room, limit, cursor)

    @deps.tool(mcp, "nightly_status")
    async def nightly_status(cycle: Annotated[str, Field(pattern=r"^(?:[0-9]{8}T[0-9]{6}|baseline-[0-9a-f]{8})$")] | None = None) -> dict:
        return invoke_sync(deps, "nightly_status", nightly_status_impl, cycle)

    @deps.tool(mcp, "knowledge_search")
    async def knowledge_search(query: Annotated[str, Field(min_length=1, max_length=200)],
                               collection: Literal["all", "skills", "reports"] = "all", limit: Limit = 20,
                               cursor: Cursor | None = None) -> SearchResult:
        return invoke_sync(deps, "knowledge_search", knowledge_search_impl, query, collection, limit, cursor)

    from src.tools import knowledge_resources, knowledge_prompts
    knowledge_resources.register(mcp, deps)
    knowledge_prompts.register(mcp, deps)
