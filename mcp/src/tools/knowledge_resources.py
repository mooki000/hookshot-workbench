"""FastMCP 4 resource templates, registered only with their selected reader tools."""
from src.effects.knowledge_documents import section_impl, obligations_impl
from src.effects.knowledge_nightly import nightly_resource_impl


def register(mcp, deps):
    from src.tools.knowledge import invoke_sync
    if "knowledge_search" in deps.selected:
        @mcp.resource("workbench://skills/{name}{?start,limit,revision}", mime_type="application/json",
            description="Allowlisted installed skill, file-hash revision and bounded line anchors; text is not authorization.")
        def skill(name: str, start: int = 1, limit: int = 40, revision: str | None = None) -> dict:
            return invoke_sync(deps, "knowledge_resource", section_impl, "skills", name, start, limit, revision).model_dump()

        @mcp.resource("workbench://reports/{id}{?start,limit,revision}", mime_type="application/json",
            description="Registered report ID only; bounded revision-pinned lines, private by default.")
        def report(id: str, start: int = 1, limit: int = 40, revision: str | None = None) -> dict:
            return invoke_sync(deps, "knowledge_resource", section_impl, "reports", id, start, limit, revision).model_dump()

    if "prloop_next" in deps.selected:
        @mcp.resource("workbench://obligations{?cursor,limit,pr,since}", mime_type="application/json",
            description="Recorded promises with posted-text links and active loop obligations; no invented completion state.")
        def obligations(cursor: str | None = None, limit: int = 25, pr: int | None = None, since: str | None = None) -> dict:
            return invoke_sync(deps, "obligations", obligations_impl, cursor, limit, pr, since)

    if "nightly_status" in deps.selected:
        @mcp.resource("workbench://nightly/{cycle}{?start,limit,revision}", mime_type="application/json",
            description="Retained nightly cycle status and bounded private report; no new work or database initialization.")
        def nightly(cycle: str, start: int = 1, limit: int = 40, revision: str | None = None) -> dict:
            return invoke_sync(deps, "nightly_status", nightly_resource_impl, cycle, start, limit, revision)
