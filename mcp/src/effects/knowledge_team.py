"""Official CLI readers only. Stock first-read joins are never invoked."""
import json
import re
from src.models import utc_now
from src.models.knowledge import ReaderResult
from src.effects.knowledge_common import (bounded_int, checked_path, command, config,
    cursor_decode, cursor_encode, digest, safe_url, text, under)


def unavailable(deps, operation, room):
    return ReaderResult(available=False, reason="trusted_cli_requires_get_only_no_save_contract",
        error=deps.error("unavailable", operation).envelope, room_id=room, observed_at=utc_now(),
        unknown=["Operator must install a reviewed --read-only CLI: GET only, no joins or settings writes; existing sign-in required."])


async def reader(deps, script, subcommand, operation, room, args, *, cursor=False):
    cfg = config(deps)
    if room not in cfg.rooms:
        raise deps.error("refused_by_policy", operation)
    checkout = checked_path(deps, deps.config.checkout, operation, directory=True)
    executable = checked_path(deps, deps.config.dependency_python, operation)
    tool = under(deps, checkout, "scripts/" + script, operation)
    if cfg.reader_contract != "get-only-no-save-v1":
        return None
    # --help exits argument parsing before stock session() or room resolution. An
    # operator contract is necessary as well: matching a flag alone is not proof of
    # read-only semantics. A missing capability fails closed without a real read.
    help_text = (await command(deps, [executable, "-I", "-B", tool, subcommand, "--help"], operation, cwd=checkout, cap=65536)).decode("utf-8", "replace")
    needed = {"--read-only", "--room", "--json"} | ({"--cursor"} if cursor else set())
    if any(flag not in help_text for flag in needed):
        return None
    # The attested reader contract uses exit 4 when existing sign-in or room access is
    # missing. It must not repair either condition or expose account diagnostics.
    raw = await command(deps, [executable, "-I", "-B", tool, subcommand, "--read-only", "--room", room, "--json", *args],
        operation, cwd=checkout, unavailable_codes=(4,))
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise deps.error("backend_failed", operation) from None


def card(row):
    claims = row.get("claims") or {}
    results = row.get("results") or {}
    return {**{key: text(row.get(key), 2048).model_dump() for key in ("id", "requester", "summary", "needs", "notes", "status", "pr_state")},
        "pr": row.get("pr") if type(row.get("pr")) is int else None,
        "approved": row.get("approved") if type(row.get("approved")) is bool else None,
        "requested": row.get("requested") if type(row.get("requested")) in (int, float) else None,
        "claims": [{"user": text(user, 256).model_dump(), "expires_at": expiry if type(expiry) in (int, float) else None}
            for user, expiry in list(claims.items())[:20]], "claims_truncated": len(claims) > 20,
        "results": [{"user": text(user, 256).model_dump(), "verdict": text(result.get("result"), 128).model_dump(),
            "url": safe_url(result.get("comment")), "time": result.get("time") if type(result.get("time")) in (int, float) else None}
            for user, result in list(results.items())[:20]], "results_truncated": len(results) > 20, "untrusted": True}


async def test_requests_read_impl(deps, card_id=None, status="open", mine=False, cursor=None, limit=20):
    operation = "test_requests_read"
    bounded_int(deps, limit, 1, 100, operation)
    if status not in {"open", "all"} or card_id is not None and not re.fullmatch(r"T-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}", card_id):
        raise deps.error("invalid_argument", operation)
    room = config(deps).request_room
    if room is None:
        raise deps.error("unavailable", operation)
    args = [card_id] if card_id else (["--all"] if status == "all" else []) + (["--mine"] if mine else [])
    value = await reader(deps, "test_requests.py", "show" if card_id else "list", operation, room, args)
    if value is None:
        return unavailable(deps, operation, room)
    values = [value] if card_id else value
    if not isinstance(values, list) or not all(isinstance(row, dict) for row in values):
        raise deps.error("backend_failed", operation)
    revision = digest([card_id, status, mine, values])
    offset = cursor_decode(deps, cursor, revision, operation)
    if offset > len(values):
        raise deps.error("invalid_argument", operation)
    selected = [card(row) for row in values[offset:offset + limit]]
    more = offset + limit < len(values)
    return ReaderResult(available=True, room_id=room, cards=selected,
        next_cursor=cursor_encode(revision, offset + limit) if more else None, complete=not more,
        unknown=["approval_unknown"] if any(row["approved"] is None for row in selected) else [], observed_at=utc_now())


async def team_read_impl(deps, room, limit=20, cursor=None):
    operation = "team_read"
    bounded_int(deps, limit, 1, 100, operation)
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 256 or any(ord(c) < 32 for c in cursor)):
        raise deps.error("invalid_argument", operation)
    args = ["--limit", str(limit)] + (["--cursor", cursor] if cursor else [])
    value = await reader(deps, "team_chat.py", "read", operation, room, args, cursor=cursor is not None)
    if value is None:
        return unavailable(deps, operation, room)
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list) or len(value["messages"]) > limit:
        raise deps.error("backend_failed", operation)
    next_cursor = value.get("next_cursor")
    if next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 256):
        raise deps.error("backend_failed", operation)
    messages = [{"id": text(row.get("id"), 256).model_dump(), "sender": text(row.get("sender"), 256).model_dump(),
        "body": text(row.get("body")).model_dump(), "timestamp": row.get("timestamp") if type(row.get("timestamp")) in (int, float) else None,
        "untrusted": True} for row in value["messages"]]
    return ReaderResult(available=True, room_id=room, messages=messages, next_cursor=next_cursor,
        complete=value.get("complete") if type(value.get("complete")) is bool else None, observed_at=utc_now())
