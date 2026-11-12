"""Search only explicitly registered documents; never walk the filesystem."""
import re
from urllib.parse import urlencode
from src.models.knowledge import DocumentSection, SearchMatch, SearchResult
from src.effects.knowledge_common import (bounded_int, config, cursor_decode, cursor_encode,
    digest, read_bytes, text, under)


def document(deps, collection, identifier):
    cfg = config(deps)
    if collection == "skills":
        if identifier not in cfg.skill_names:
            raise deps.error("refused_by_policy", "knowledge_resource")
        path = under(deps, cfg.skills_dir, identifier + "/SKILL.md", "knowledge_resource")
        privacy = "private"
    else:
        registered = next((r for r in cfg.reports if r.id == identifier), None)
        if registered is None:
            raise deps.error("not_found", "knowledge_resource")
        path = under(deps, cfg.reports_dir, registered.file, "knowledge_resource")
        privacy = registered.privacy
    raw = read_bytes(deps, path, "knowledge_resource")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError:
        raise deps.error("backend_failed", "knowledge_resource") from None
    return lines, digest(raw), privacy


def section_impl(deps, collection, identifier, start=1, limit=40, revision=None):
    bounded_int(deps, start, 1, 100000, "knowledge_resource")
    bounded_int(deps, limit, 1, 100, "knowledge_resource")
    lines, actual, privacy = document(deps, collection, identifier)
    if revision is not None and revision != actual:
        raise deps.error("busy", "knowledge_resource")
    uri = f"workbench://{collection}/{identifier}"
    return render_section(deps, lines, actual, privacy, uri, start, limit)


def render_section(deps, lines, revision, privacy, uri, start=1, limit=40):
    bounded_int(deps, start, 1, 100000, "knowledge_resource")
    bounded_int(deps, limit, 1, 100, "knowledge_resource")
    if start > max(1, len(lines)):
        raise deps.error("invalid_argument", "knowledge_resource")
    selected = []
    used = 0
    for index in range(start - 1, min(len(lines), start - 1 + limit)):
        bounded = text(lines[index], 2048)
        size = len(bounded.text.encode())
        if selected and used + size > 16384:
            break
        used += size
        selected.append({"line": index + 1, "content": bounded.model_dump()})
    following = start + len(selected)
    more = following <= len(lines)
    return DocumentSection(resource_uri=uri, revision=revision, privacy=privacy, lines=selected,
        next_line=following if more else None,
        next_uri=uri + "?" + urlencode({"start": following, "limit": limit, "revision": revision}) if more else None,
        total_lines=len(lines), truncated=more or any(r["content"]["truncated"] for r in selected),
        stale_warnings=["installed_text_is_not_live_authorization"] if "/skills/" in uri else [])


def headings(lines):
    """Index headings and each section's first nonempty prose line, not raw bodies."""
    first = True
    frontmatter = bool(lines and lines[0].strip() == "---")
    for index, line in enumerate(lines, 1):
        if frontmatter:
            if index > 1 and line.strip() == "---":
                frontmatter = False
            continue
        if re.match(r"^#{1,6}\s+", line):
            yield index, line
            first = True
        elif first and line.strip():
            yield index, line
            first = False


def knowledge_search_impl(deps, query, collection="all", limit=20, cursor=None):
    bounded_int(deps, limit, 1, 100, "knowledge_search")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or collection not in {"all", "skills", "reports"}:
        raise deps.error("invalid_argument", "knowledge_search")
    cfg = config(deps)
    identifiers = []
    if collection in {"all", "skills"}:
        identifiers += [("skills", name) for name in cfg.skill_names]
    if collection in {"all", "reports"}:
        identifiers += [("reports", report.id) for report in cfg.reports]
    if not identifiers:
        raise deps.error("unavailable", "knowledge_search")
    indexed = []
    revisions = []
    warnings = []
    entries = 0
    words = query.casefold().split()
    for kind, identifier in sorted(identifiers):
        try:
            lines, revision, privacy = document(deps, kind, identifier)
        except Exception as error:
            if getattr(getattr(error, "envelope", None), "code", None) in {"not_found", "unavailable"}:
                warnings.append(f"{kind}/{identifier}:registered_document_unavailable")
                continue
            raise
        revisions.append([kind, identifier, revision])
        for number, line in headings(lines):
            if entries == 10000:
                warnings.append("index_entry_budget_exhausted")
                break
            entries += 1
            if all(word in line.casefold() for word in words):
                indexed.append(SearchMatch(resource_uri=f"workbench://{kind}/{identifier}?start={number}&limit=20&revision={revision}",
                    lines=(number, number), revision=revision, privacy=privacy, excerpt=text(line, 512)))
        if entries == 10000:
            if "index_entry_budget_exhausted" not in warnings:
                warnings.append("index_entry_budget_exhausted")
            break
    revision = digest([query, collection, revisions])
    offset = cursor_decode(deps, cursor, revision, "knowledge_search")
    if offset > len(indexed):
        raise deps.error("invalid_argument", "knowledge_search")
    return SearchResult(matches=indexed[offset:offset + limit], index_revision=revision,
        next_cursor=cursor_encode(revision, offset + limit) if offset + limit < len(indexed) else None,
        stale_warnings=warnings[:100])


def table_rows(lines):
    for number, line in enumerate(lines, 1):
        if not line.startswith("|"):
            continue
        cells = [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if cells and not all(re.fullmatch(r"[-: ]*", cell) for cell in cells):
            yield number, cells


def obligations_impl(deps, cursor=None, limit=25, pr=None, since=None):
    from src.effects.knowledge_loop import load_snapshot
    from src.effects.knowledge_common import safe_url, sha
    bounded_int(deps, limit, 1, 100, "obligations")
    if pr is not None:
        bounded_int(deps, pr, 1, 2147483647, "obligations")
    if since is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise deps.error("invalid_argument", "obligations")
    cfg = config(deps)
    rows, revisions, unavailable = [], [], []
    index = {}
    if cfg.pr_index_report_id:
        lines, rev, _ = document(deps, "reports", cfg.pr_index_report_id)
        revisions.append(rev)
        for number, cells in table_rows(lines):
            if len(cells) >= 6 and (match := re.match(r"^(\d+)", cells[0])):
                index[int(match[1])] = {"state": text(cells[4], 1024).model_dump(),
                    "source": f"workbench://reports/{cfg.pr_index_report_id}?start={number}&limit=1&revision={rev}"}
    if cfg.posted_report_id:
        lines, rev, _ = document(deps, "reports", cfg.posted_report_id)
        revisions.append(rev)
        for number, cells in table_rows(lines):
            if len(cells) != 5 or cells[0] == "When":
                continue
            owed = cells[4]
            if owed.casefold().strip(". ") in {"", "-", "—", "none", "done", "completed", "no follow-up"}:
                continue
            if since and cells[0][:10] < since:
                continue
            urls = [safe_url(v.rstrip(".,;)")) for v in re.findall(r"https://github\.com/[^\s|]+", " ".join(cells))]
            urls = [url for url in urls if url]
            match = re.search(r"(?:/pull/|\bPR\s+|\bpull request\s+)(\d+)", " ".join(cells), re.I)
            number_pr = int(match[1]) if match else None
            if pr is not None and number_pr != pr:
                continue
            linked_reports = [f"workbench://reports/{r.id}" for r in cfg.reports if r.file in cells[3]]
            rows.append({"source": f"workbench://reports/{cfg.posted_report_id}?start={number}&limit=1&revision={rev}",
                "pr": number_pr, "promised": text(owed, 2048).model_dump(), "posted_at": text(cells[0], 80).model_dump(),
                "status": "recorded_owed_unreconciled", "links": urls[:10] + linked_reports[:10],
                "index": index.get(number_pr), "tested_head": None, "carried_head": None})
    if cfg.prloop_state_dir:
        try:
            state, _, revision = load_snapshot(deps)
        except Exception as error:
            if getattr(getattr(error, "envelope", None), "code", None) not in {"not_found", "unavailable"}:
                raise
            unavailable.append("loop_snapshot_unavailable")
        else:
            revisions.append(revision)
            for record in state.get("active", {}).values():
                number = record.get("number")
                if pr is not None and number != pr or since and str(record.get("wait_since", ""))[:10] < since:
                    continue
                if record.get("wait_for") not in {"our-reply", "our-test"}:
                    continue
                urls = [safe_url(record.get("last_comment"))]
                current = (state.get("snapshot") or {}).get("prs", {}).get(str(number), {})
                urls.append(safe_url(current.get("url")))
                rows.append({"source": "prloop:" + revision, "pr": number,
                    "promised": text(record.get("wait_for")).model_dump(), "status": "active_loop_record",
                    "links": [v for v in urls if v], "index": index.get(number),
                    "tested_head": sha(record.get("last_tested_head")), "carried_head": sha((record.get("carry") or {}).get("to_head"))})
    if not revisions:
        raise deps.error("unavailable", "obligations")
    revision = digest([revisions, pr, since])
    offset = cursor_decode(deps, cursor, revision, "obligations")
    if offset > len(rows):
        raise deps.error("invalid_argument", "obligations")
    return {"obligations": rows[offset:offset + limit], "revision": revision,
        "next_cursor": cursor_encode(revision, offset + limit) if offset + limit < len(rows) else None,
        "limitations": ["Posted ledger free text is not automatically reconciled; recorded promises are not proof they remain outstanding.", *unavailable],
        "privacy": "private", "untrusted": True}
