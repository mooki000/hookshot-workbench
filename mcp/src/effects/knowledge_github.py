"""Explicit GET-only GitHub snapshots, including every PR discussion endpoint."""
import asyncio
import hashlib
import json
from src.errors import WorkbenchError
from src.models import utc_now
from src.models.knowledge import ThreadItem, ThreadSnapshot
from src.effects.knowledge_common import (bounded_int, checked_path, command, config, cursor_decode,
    cursor_encode, digest, safe_url, sha, stamp, text)


def allowed_thread(deps, repository, number, operation):
    if repository not in config(deps).repositories:
        raise deps.error("refused_by_policy", operation)
    bounded_int(deps, number, 1, 2147483647, operation)
    return checked_path(deps, config(deps).gh, operation)


def item(row, repository):
    if not isinstance(row, dict):
        raise ValueError
    return ThreadItem(id=row.get("id") if type(row.get("id")) is int else None,
        body=text(row.get("body")), author=text((row.get("user") or {}).get("login"), 256),
        updated_at=stamp(row.get("updated_at") or row.get("submitted_at") or row.get("created_at")),
        url=safe_url(row.get("html_url"), repository),
        state=text(row["state"], 128) if "state" in row else None,
        path=text(row["path"], 512) if "path" in row else None,
        diff_hunk=text(row["diff_hunk"], 2048) if "diff_hunk" in row else None)


async def github_thread_read_impl(deps, repository: str, number: int, cursor=None, limit=25, *, owned_group=False) -> ThreadSnapshot:
    operation = "github_thread_read"
    bounded_int(deps, limit, 1, 100, operation)
    gh = allowed_thread(deps, repository, number, operation)
    deadline = asyncio.get_running_loop().time() + config(deps).read_timeout_seconds
    hasher = hashlib.sha256()

    async def api(endpoint, *, hash_page=True):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise deps.error("timeout", operation)
        raw = await command(deps, [gh, "api", "--method", "GET", "--hostname", "github.com", endpoint], operation,
            timeout=remaining, owned_group=owned_group)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise deps.error("backend_failed", operation) from None
        if hash_page:
            # Hash the entire fetched text, not only the truncated result sent to a client.
            hasher.update(endpoint.encode())
            hasher.update(digest(value).encode())
        return value

    prefix = f"repos/{repository}"
    issue = await api(f"{prefix}/issues/{number}")
    if not isinstance(issue, dict):
        raise deps.error("backend_failed", operation)
    is_pr = "pull_request" in issue
    pull = await api(f"{prefix}/pulls/{number}") if is_pr else None
    if pull is not None and not isinstance(pull, dict):
        raise deps.error("backend_failed", operation)
    endpoints = [("comments", f"{prefix}/issues/{number}/comments")]
    if is_pr:
        endpoints += [("reviews", f"{prefix}/pulls/{number}/reviews"), ("inline", f"{prefix}/pulls/{number}/comments")]
    all_items = []
    complete = True
    warnings = []
    pages = 0
    for kind, endpoint in endpoints:
        page = 1
        while True:
            if pages >= config(deps).max_pages:
                complete = False
                warnings.append("pagination_budget_exhausted")
                break
            try:
                rows = await api(f"{endpoint}?per_page=100&page={page}")
            except WorkbenchError as error:
                if error.envelope.code not in {"timeout", "backend_failed"}:
                    raise
                complete = False
                warnings.append(kind + "_read_incomplete")
                break
            pages += 1
            if not isinstance(rows, list) or len(rows) > 100:
                raise deps.error("backend_failed", operation)
            try:
                all_items.extend((kind, item(row, repository)) for row in rows)
            except (TypeError, ValueError, AttributeError):
                raise deps.error("backend_failed", operation) from None
            if len(rows) < 100:
                break
            page += 1
    consistent = None
    try:
        end_issue = await api(f"{prefix}/issues/{number}", hash_page=False)
        end_pull = await api(f"{prefix}/pulls/{number}", hash_page=False) if is_pr else None
        consistent = digest(issue) == digest(end_issue) and digest(pull) == digest(end_pull)
        if not consistent:
            warnings.append("thread_changed_during_read")
    except WorkbenchError:
        warnings.append("snapshot_consistency_unknown")
    revision = hasher.hexdigest()
    offset = cursor_decode(deps, cursor, revision, operation)
    if offset > len(all_items):
        raise deps.error("invalid_argument", operation)
    selected = all_items[offset:offset + limit]
    more = offset + limit < len(all_items)
    return ThreadSnapshot(repository=repository, number=number, kind="pull_request" if is_pr else "issue",
        head=sha((pull.get("head") or {}).get("sha")) if pull else None,
        title=text(issue.get("title")), body=text(issue.get("body")), state=text(issue.get("state"), 128),
        comments=[value for kind, value in selected if kind == "comments"],
        reviews=[value for kind, value in selected if kind == "reviews"],
        inline=[value for kind, value in selected if kind == "inline"], pages_complete=complete,
        consistent=consistent, updated_at=stamp(issue.get("updated_at")), observed_at=utc_now(), snapshot_hash=revision,
        next_cursor=cursor_encode(revision, offset + limit) if more else None, items_total=len(all_items),
        items_truncated=more or offset > 0, warnings=list(dict.fromkeys(warnings)))
