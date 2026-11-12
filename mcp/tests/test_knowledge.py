import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError
from src.main import build_server
from src.models import TERMINAL, utc_now
from src.models.knowledge import KnowledgeConfig, ReportRegistration
from src.policy import Safety
from src.tools import registry, knowledge, knowledge_resources, knowledge_prompts
from src.effects.knowledge_common import digest
from src.effects.knowledge_documents import section_impl, obligations_impl
from src.effects.knowledge_jobs import github_watch_impl
from src.effects.knowledge_loop import load_snapshot, prloop_next_impl
from src.effects.knowledge_prompts import PLANS, prompt_plan_impl
from src.effects.knowledge_github import github_thread_read_impl

REPO = "example/project"
FAKES = Path(__file__).parent / "fakes"


def put(path, value):
    path.write_text(json.dumps(value))


def gh_pages():
    root = "repos/" + REPO
    row = lambda n: {"id": n, "body": "synthetic comment " + str(n), "user": {"login": "tester"},
        "updated_at": "2026-01-01T00:00:00Z", "html_url": "https://github.com/" + REPO + "/pull/7#issuecomment-" + str(n)}
    return {f"{root}/issues/7": {"title": "Synthetic issue", "body": "x" * 5000, "state": "open", "pull_request": {}, "updated_at": "2026-01-01T00:00:00Z"},
        f"{root}/pulls/7": {"head": {"sha": "a" * 40}},
        f"{root}/issues/7/comments?per_page=100&page=1": [row(n) for n in range(100)],
        f"{root}/issues/7/comments?per_page=100&page=2": [row(100)],
        f"{root}/pulls/7/reviews?per_page=100&page=1": [{**row(200), "state": "COMMENTED"}],
        f"{root}/pulls/7/comments?per_page=100&page=1": [{**row(300), "path": "src/unit.py", "diff_hunk": "neutral diff"}]}


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "GROUPS", (*registry.GROUPS, "knowledge"))
    bin_dir = tmp_path / "bin"
    scripts = tmp_path / "checkout" / "scripts"
    skills = tmp_path / "skills"
    reports = tmp_path / "reports"
    loop = tmp_path / "loop"
    nightly = tmp_path / "nightly"
    for path in (bin_dir, scripts, skills, reports, loop, nightly):
        path.mkdir(parents=True)
    source = (FAKES / "knowledge_backend.py").read_text()
    gh = bin_dir / "gh"
    gh.write_text("#!" + sys.executable + "\n" + source)
    gh.chmod(0o700)
    for name in ("team_chat.py", "test_requests.py"):
        (scripts / name).write_text(source)
    put(bin_dir / "backend.json", {"pages": gh_pages()})
    cards = [{"id": "T-ABCD", "summary": "Synthetic request", "needs": "neutral hardware", "notes": "untrusted note",
        "status": "open", "pr": 7, "approved": None, "claims": {"tester": 12345},
        "results": {"other": {"result": "pass", "comment": "https://github.com/example/project/pull/7#issuecomment-2", "time": 123}}, "requester": "owner"}]
    put(scripts / "backend.json", {"safe": True, "cards": cards + [{**cards[0], "id": "T-EFGH"}],
        "messages": [{"id": str(n), "sender": "someone", "body": "Untrusted message " + str(n), "timestamp": n} for n in range(3)]})
    selected = {}
    for requirements in PLANS.values():
        for name, section in requirements:
            selected.setdefault(name, set()).add(section)
    for name, sections in selected.items():
        folder = skills / name
        folder.mkdir()
        (folder / "SKILL.md").write_text("# Synthetic installed skill\nSynthetic workflow\n\n" + "\n".join(
            "## " + section + "\nSynthetic source instruction for " + section + ".\n" for section in sorted(sections)))
    (reports / "alpha.md").write_text("# Synthetic alpha\nSynthetic report first line\n## Result\nSynthetic measured result\n" + "extra line\n" * 110)
    (reports / "posted.md").write_text("| When | Where | What | Text | Follow-up owed |\n|---|---|---|---|---|\n"
        "| 2026-01-01 | https://github.com/example/project/pull/7 | synthetic promise | alpha.md | run a neutral check |\n"
        "| 2026-01-02 | https://github.com/example/project/pull/8 | finished | alpha.md | none |\n")
    (reports / "index.md").write_text("| # | Title | Branch | Opened | State | Notes |\n|---|---|---|---|---|---|\n"
        "| 7 | Synthetic | branch | 2026-01-01 | open | waiting |\n")
    loop_script = tmp_path / "loop_tool.py"
    loop_script.write_text((FAKES / "knowledge_loop.py").read_text())
    state = {"version": 1, "active": {"7": {"number": 7, "wait_for": "our-test", "wait_since": "2026-01-01T00:00:00Z",
        "last_tested_head": "c" * 40, "carry": {"to_head": "a" * 40}, "blocks": ["exact-head review required"]}}, "done": {}, "parked": {},
        "snapshot": {"at": utc_now(), "prs": {"7": {"number": 7, "head": "a" * 40, "url": "https://github.com/example/project/pull/7"}}, "requests_available": False}}
    put(loop / "state.json", state)
    put(loop / "config.json", {"version": 1, "checkout": str(scripts.parent), "fixture_root": str(loop)})
    cycle = "20260101T010203"
    (nightly / "raw" / cycle).mkdir(parents=True)
    (nightly / "reports").mkdir()
    (nightly / "outbox").mkdir()
    put(nightly / "state.json", {"last_completed": {"cycle": cycle, "commit": "a" * 40}})
    put(nightly / "latest.json", {"cycle": cycle, "status": "complete"})
    put(nightly / "raw" / cycle / "cycle.json", {"status": "complete", "build": {"commit": "a" * 40, "build_id": "neutral-build"}})
    put(nightly / "release-baseline.json", {"complete": True, "measured_at": "2026-01-01T00:00:00Z", "build": {"commit": "c" * 40}})
    put(nightly / "outbox" / (cycle + ".json"), {"status": "complete", "draft": "/private/must-not-leak"})
    (nightly / "reports" / (cycle + ".md")).write_text("# Synthetic cycle\nNeutral retained evidence.\n")
    database = nightly / "results.sqlite"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE nights (id TEXT, commit_sha TEXT, started TEXT, ended TEXT, status TEXT)")
    db.execute("INSERT INTO nights VALUES (?,?,?,?,?)", ("rolling-" + cycle, "a" * 40, "2026-01-01T01:02:03Z", "2026-01-01T02:03:04Z", "complete"))
    db.commit()
    db.close()
    settings = KnowledgeConfig(gh=gh, repositories=(REPO,), skills_dir=skills, skill_names=tuple(selected), reports_dir=reports,
        reports=(ReportRegistration(id="alpha", file="alpha.md"), ReportRegistration(id="posted", file="posted.md"), ReportRegistration(id="index", file="index.md")),
        posted_report_id="posted", pr_index_report_id="index", prloop_script=loop_script, prloop_state_dir=loop,
        nightly_root=nightly, nightly_database=database, rooms=("room-a", "requests"), request_room="requests",
        reader_contract="get-only-no-save-v1", watch_interval_seconds=0.05, read_timeout_seconds=5)
    executable = Path(sys.executable).resolve()
    cfg = Config(state_root=tmp_path / "state", checkout=scripts.parent, dependency_python=executable, knowledge=settings,
        profile="operate", read_roots=(tmp_path, executable.parent), write_roots=(tmp_path,))
    return SimpleNamespace(root=tmp_path, config=cfg, settings=settings, bin=bin_dir, scripts=scripts, loop=loop,
        reports=reports, skills=skills, nightly=nightly, cycle=cycle)


def data(result):
    return result.structured_content


async def error(client, tool, args, code):
    result = await client.call_tool(tool, args, raise_on_error=False)
    assert result.is_error
    value = json.loads(result.content[0].text)
    assert value["code"] == code
    assert set(value) == {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}
    assert "/private" not in result.content[0].text and "credential" not in result.content[0].text
    return value


async def observed(predicate, seconds=10):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        value = predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("expected observed state was not reached")
        await asyncio.sleep(0.02)


def dead(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].startswith("Z")
    except FileNotFoundError:
        return True


@pytest.mark.parametrize("profile,extras", [("observe", ()), ("operate", ()), ("observe", ("github_watch",))])
async def test_knowledge_catalogue(kb, profile, extras):
    cfg = replace(kb.config, profile=profile, extra_tools=extras)
    async with Client(build_server(cfg)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        expected = {spec.name for spec in knowledge.SPECS if spec.safety == Safety.READ or profile == "operate"} | set(extras)
        assert {name for name in tools if name in {s.name for s in knowledge.SPECS}} == expected
        required = {"github_thread_read": ["repository", "number"], "github_watch": ["threads", "timeout_seconds", "idempotency_key"],
            "prloop_refresh": ["snapshot_id", "idempotency_key"], "prloop_next": [], "test_requests_read": [],
            "team_read": ["room"], "nightly_status": [], "knowledge_search": ["query"]}
        for spec in knowledge.SPECS:
            if spec.name in expected:
                tool = tools[spec.name]
                assert tool.input_schema.get("required", []) == required[spec.name]
                assert tool.annotations.read_only_hint == (spec.safety == Safety.READ)
                assert tool.annotations.open_world_hint == spec.open_world
                assert tool.annotations.destructive_hint is False and tool.annotations.idempotent_hint is True
        assert {p.name for p in await client.list_prompts()} == set(PLANS)
        templates = {t.uri_template for t in await client.list_resource_templates()}
        assert {"workbench://skills/{name}{?start,limit,revision}", "workbench://reports/{id}{?start,limit,revision}",
            "workbench://obligations{?cursor,limit,pr,since}", "workbench://nightly/{cycle}{?start,limit,revision}"} <= templates


async def test_knowledge_unselected_resources_and_prompts(kb):
    deps = Deps(kb.config)
    deps.selected = frozenset()
    server = FastMCP("empty")
    knowledge_resources.register(server, deps)
    knowledge_prompts.register(server, deps)
    async with Client(server) as client:
        assert not await client.list_prompts()
        assert not await client.list_resources()
        assert not await client.list_resource_templates()


async def test_knowledge_complete_thread_and_cursor(kb):
    async with Client(build_server(kb.config)) as client:
        first = data(await client.call_tool("github_thread_read", {"repository": REPO, "number": 7, "limit": 100}))
        assert first["pages_complete"] is True and first["consistent"] is True
        assert first["body"]["truncated"] is True and len(first["body"]["text"]) == 4096
        assert len(first["comments"]) == 100 and first["items_total"] == 103
        assert first["head"] == "a" * 40 and first["body"]["untrusted"] is True
        second = data(await client.call_tool("github_thread_read", {"repository": REPO, "number": 7, "limit": 100, "cursor": first["next_cursor"]}))
        assert len(second["comments"]) == len(second["reviews"]) == len(second["inline"]) == 1
        assert second["next_cursor"] is None
        assert second["inline"][0]["path"]["untrusted"] is True
        calls = [json.loads(line) for line in (kb.bin / "calls.jsonl").read_text().splitlines()]
        assert all(call[:5] == ["api", "--method", "GET", "--hostname", "github.com"] for call in calls)
        fixture = json.loads((kb.bin / "backend.json").read_text())
        fixture["pages"][f"repos/{REPO}/issues/7"]["body"] += "changed beyond truncation"
        put(kb.bin / "backend.json", fixture)
        await error(client, "github_thread_read", {"repository": REPO, "number": 7, "cursor": first["next_cursor"]}, "busy")
        await error(client, "github_thread_read", {"repository": "outside/project", "number": 7}, "refused_by_policy")
        await error(client, "github_thread_read", {"repository": REPO, "number": 7, "limit": 101}, "invalid_argument")
        await error(client, "github_thread_read", {"repository": REPO, "number": 7, "cursor": "not-a-cursor"}, "invalid_argument")


async def test_knowledge_issue_and_incomplete_pages(kb):
    fixture = json.loads((kb.bin / "backend.json").read_text())
    fixture["pages"][f"repos/{REPO}/issues/7"].pop("pull_request")
    put(kb.bin / "backend.json", fixture)
    cfg = replace(kb.config, knowledge=kb.settings.model_copy(update={"max_pages": 1}))
    async with Client(build_server(cfg)) as client:
        result = data(await client.call_tool("github_thread_read", {"repository": REPO, "number": 7}))
        assert result["kind"] == "issue" and result["head"] is None
        assert result["pages_complete"] is False
        assert result["warnings"] == ["pagination_budget_exhausted"]


async def test_knowledge_cli_readers(kb):
    async with Client(build_server(kb.config)) as client:
        first = data(await client.call_tool("test_requests_read", {"limit": 1}))
        assert first["cards"][0]["approved"] is None and first["unknown"] == ["approval_unknown"]
        assert first["cards"][0]["claims"][0]["expires_at"] == 12345
        assert first["cards"][0]["results"][0]["verdict"]["text"] == "pass"
        next_page = data(await client.call_tool("test_requests_read", {"limit": 1, "cursor": first["next_cursor"]}))
        assert next_page["cards"][0]["id"]["text"] == "T-EFGH"
        shown = data(await client.call_tool("test_requests_read", {"card_id": "T-ABCD"}))
        assert shown["cards"][0]["id"]["text"] == "T-ABCD"
        messages = data(await client.call_tool("team_read", {"room": "room-a", "limit": 2}))
        assert len(messages["messages"]) == 2 and messages["next_cursor"] == "2"
        last = data(await client.call_tool("team_read", {"room": "room-a", "limit": 2, "cursor": "2"}))
        assert last["complete"] is True and len(last["messages"]) == 1
        await error(client, "team_read", {"room": "unknown"}, "refused_by_policy")
        await error(client, "team_read", {"room": "room-a", "limit": 201}, "invalid_argument")
        calls = [json.loads(line) for line in (kb.scripts / "calls.jsonl").read_text().splitlines()]
        assert all("--help" in call or "--read-only" in call for call in calls)
        assert all(call[0] in {"read", "list", "show"} for call in calls)


@pytest.mark.parametrize("attested", [False, True])
async def test_knowledge_stock_readers_fail_closed(kb, attested):
    fixture = json.loads((kb.scripts / "backend.json").read_text())
    fixture["safe"] = False
    put(kb.scripts / "backend.json", fixture)
    cfg = replace(kb.config, knowledge=kb.settings.model_copy(update={"reader_contract": "get-only-no-save-v1" if attested else None}))
    async with Client(build_server(cfg)) as client:
        for name, arguments in (("team_read", {"room": "room-a"}), ("test_requests_read", {})):
            result = data(await client.call_tool(name, arguments))
            assert not result["available"] and result["error"]["code"] == "unavailable"
            assert "get_only_no_save" in result["reason"]
    log = kb.scripts / "calls.jsonl"
    assert not log.exists() or all("--help" in json.loads(line) for line in log.read_text().splitlines())


async def test_knowledge_search_and_registered_resources(kb):
    async with Client(build_server(kb.config)) as client:
        first = data(await client.call_tool("knowledge_search", {"query": "Synthetic", "collection": "reports", "limit": 1}))
        assert len(first["matches"]) == 1 and first["next_cursor"]
        assert first["matches"][0]["privacy"] == "private" and first["matches"][0]["lines"][0] == 1
        second = data(await client.call_tool("knowledge_search", {"query": "Synthetic", "collection": "reports", "limit": 1, "cursor": first["next_cursor"]}))
        assert second["matches"][0]["lines"][0] == 2
        section = json.loads((await client.read_resource("workbench://reports/alpha?limit=2"))[0].text)
        assert section["next_line"] == 3 and len(section["lines"]) == 2
        continuation = json.loads((await client.read_resource(section["next_uri"]))[0].text)
        assert continuation["lines"][0]["line"] == 3
        skill = json.loads((await client.read_resource("workbench://skills/hookshot-workbench?limit=1"))[0].text)
        raw = (kb.skills / "hookshot-workbench" / "SKILL.md").read_bytes()
        assert skill["revision"] == hashlib.sha256(raw).hexdigest()
        (kb.reports / "alpha.md").write_text("# Changed\n")
        await error(client, "knowledge_search", {"query": "Synthetic", "collection": "reports", "cursor": first["next_cursor"]}, "busy")
        with pytest.raises(Exception):
            await client.read_resource(section["next_uri"])
        await error(client, "knowledge_search", {"query": "anything", "collection": "filesystem"}, "invalid_argument")
        await error(client, "knowledge_search", {"query": ""}, "invalid_argument")


async def test_knowledge_obligations(kb):
    async with Client(build_server(kb.config)) as client:
        first = json.loads((await client.read_resource("workbench://obligations?limit=1"))[0].text)
        row = first["obligations"][0]
        assert row["status"] == "recorded_owed_unreconciled" and row["pr"] == 7
        assert "workbench://reports/alpha" in row["links"] and row["index"]["state"]["text"] == "open"
        following = json.loads((await client.read_resource("workbench://obligations?limit=1&cursor=" + first["next_cursor"]))[0].text)
        row = following["obligations"][0]
        assert row["status"] == "active_loop_record"
        assert row["tested_head"] == "c" * 40 and row["carried_head"] == "a" * 40
        filtered = json.loads((await client.read_resource("workbench://obligations?pr=8"))[0].text)
        assert filtered["obligations"] == []


async def test_knowledge_nightly_read_only(kb):
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in kb.nightly.rglob("*") if path.is_file()}
    async with Client(build_server(kb.config)) as client:
        result = data(await client.call_tool("nightly_status"))
        assert result["cycle"] == kb.cycle and result["stage"]["text"] == "complete"
        assert result["baseline"]["complete"] is True and result["last_complete"]["commit"] == "a" * 40
        assert result["watch_owner"] is None and result["queued_jobs"] is None
        assert "/private" not in json.dumps(result)
        retained = json.loads((await client.read_resource("workbench://nightly/" + kb.cycle + "?limit=1"))[0].text)
        assert retained["report"]["privacy"] == "private" and retained["report"]["next_line"] == 2
        await error(client, "nightly_status", {"cycle": "../outside"}, "invalid_argument")
    after = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in kb.nightly.rglob("*") if path.is_file()}
    assert after == before


async def test_knowledge_pure_loop_and_no_initialization(kb):
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in kb.loop.iterdir()}
    async with Client(build_server(kb.config)) as client:
        result = data(await client.call_tool("prloop_next", {"why": True}))
        assert result["action"]["text"] == "diff-review" and result["head"] == "a" * 40
        assert result["stale"] is False and result["request_feed_status"] == "unknown"
        await error(client, "prloop_next", {"snapshot_id": "0" * 64}, "busy")
        assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in kb.loop.iterdir()} == before
        (kb.loop / "config.json").unlink()
        await error(client, "prloop_next", {}, "not_found")
        assert not (kb.loop / "config.json").exists() and not (kb.loop / "loop.lock").exists()


@pytest.mark.parametrize("name", list(PLANS))
async def test_knowledge_prompt_installed_citations(kb, name):
    arguments = {"prove_fix": dict(head="a" * 40, base="b" * 40, topic="neutral", acceptance="Synthetic behavior"),
        "compare_builds": dict(build_ids="one,two", scenario="neutral", metric="interval"),
        "reproduce_issue": dict(thread_snapshot="snapshot", hardware="neutral"),
        "test_requested_pr": dict(request_snapshot="request", thread_snapshot="snapshot", policy="operator"),
        "recover_or_cancel": dict(job_or_run_id="job_synthetic"), "prepare_evidence_handoff": dict(bundle_id="bundle_synthetic")}[name]
    async with Client(build_server(kb.config)) as client:
        result = await client.get_prompt(name, arguments)
        plan = json.loads(result.messages[0].content.text)
        assert plan["permission_granted"] is False and len(plan["authorization_gates"]) >= 4
        assert len(plan["handoff_checks"]) >= 5
        assert [s["order"] for s in plan["plan"]] == list(range(1, len(plan["plan"]) + 1))
        for step in plan["plan"]:
            assert "Synthetic source instruction" in step["installed_guidance"]["text"]
            assert step["section"] and step["revision"] and "start=" in step["source_uri"]


async def test_knowledge_prompt_missing_source_refuses(kb):
    (kb.skills / "hookshot-workbench" / "SKILL.md").write_text("# Missing required section\n")
    deps = Deps(kb.config)
    with pytest.raises(WorkbenchError) as failure:
        prompt_plan_impl(deps, "recover_or_cancel", {"job_or_run_id": "synthetic"})
    assert failure.value.envelope.code == "unavailable"


async def test_knowledge_watch_changes_cursors_and_deduplication(kb):
    deps = Deps(kb.config)
    async with Client(build_server(deps=deps)) as client:
        arguments = {"threads": [{"repository": REPO, "number": 7}], "timeout_seconds": 2, "idempotency_key": "watch-one"}
        start = time.monotonic()
        job = data(await client.call_tool("github_watch", arguments))
        assert time.monotonic() - start < 1.5 and job["state"] not in TERMINAL
        identifier = job["job_id"]
        await observed(lambda: deps.registry.get_job(identifier).artifact_uris)
        again = data(await client.call_tool("github_watch", arguments))
        assert again["job_id"] == identifier
        fixture = json.loads((kb.bin / "backend.json").read_text())
        fixture["pages"][f"repos/{REPO}/issues/7"]["title"] = "Changed synthetic title"
        put(kb.bin / "backend.json", fixture)
        await observed(lambda: any(e.kind == "thread_changed" for e in deps.registry.events(identifier, limit=100).events))
        await observed(lambda: deps.registry.get_job(identifier).state in TERMINAL)
        assert deps.registry.get_job(identifier).state == "completed"
        receipt = deps.registry.receipt(identifier)
        assert receipt.process_exited and receipt.descendants_exited
        cursor = deps.registry.get_job(identifier).artifact_uris[-1].rsplit("/", 1)[1]
        next_job = data(await client.call_tool("github_watch", {**arguments, "idempotency_key": "watch-two", "timeout_seconds": 0.5, "prior_cursor": cursor}))
        await observed(lambda: deps.registry.get_job(next_job["job_id"]).state in TERMINAL)
        assert not any(e.kind == "thread_changed" for e in deps.registry.events(next_job["job_id"], limit=100).events)
        await error(client, "github_watch", {**arguments, "timeout_seconds": 3}, "invalid_argument")


async def test_knowledge_refresh_job_and_idempotency(kb):
    deps = Deps(kb.config)
    async with Client(build_server(deps=deps)) as client:
        snapshot = data(await client.call_tool("prloop_next"))["snapshot_id"]
        args = {"snapshot_id": snapshot, "idempotency_key": "scan-one"}
        job = data(await client.call_tool("prloop_refresh", args))
        await observed(lambda: deps.registry.get_job(job["job_id"]).state in TERMINAL)
        assert deps.registry.get_job(job["job_id"]).state == "completed"
        assert (kb.loop / "scans").read_text().splitlines() == ["scan"]
        again = data(await client.call_tool("prloop_refresh", args))
        assert again["job_id"] == job["job_id"]
        assert (kb.loop / "scans").read_text().splitlines() == ["scan"]
        result_event = next(e for e in deps.registry.events(job["job_id"], limit=100).events if e.kind == "knowledge_result")
        assert result_event.data["snapshot_id"] != snapshot
        current = data(await client.call_tool("prloop_next", {"snapshot_id": result_event.data["snapshot_id"]}))
        assert current["head"] == "b" * 40
        await error(client, "prloop_refresh", {"snapshot_id": snapshot, "idempotency_key": "stale"}, "busy")


@pytest.mark.parametrize("operation", ["github_watch", "prloop_refresh"])
async def test_knowledge_long_cancellation_owns_descendants(kb, operation):
    deps = Deps(kb.config)
    location = kb.bin if operation == "github_watch" else kb.loop
    config_path = location / ("backend.json" if operation == "github_watch" else "config.json")
    value = json.loads(config_path.read_text())
    value["block"] = True
    put(config_path, value)
    async with Client(build_server(deps=deps)) as client:
        untouched = deps.registry.create_job("unrelated")
        if operation == "github_watch":
            args = {"threads": [{"repository": REPO, "number": 7}], "timeout_seconds": 60, "idempotency_key": "cancel-watch"}
        else:
            args = {"snapshot_id": data(await client.call_tool("prloop_next"))["snapshot_id"], "idempotency_key": "cancel-scan"}
        job = data(await client.call_tool(operation, args))
        await observed(lambda: (location / "child.pid").exists())
        child = int((location / "child.pid").read_text())
        parent = int((location / "worker.pid").read_text())
        assert not dead(child) and not dead(parent)
        duplicate = data(await client.call_tool(operation, args))
        assert duplicate["job_id"] == job["job_id"]
        await client.call_tool("job_cancel", {"job_id": job["job_id"]})
        await observed(lambda: deps.registry.get_job(job["job_id"]).state in TERMINAL)
        assert deps.registry.get_job(job["job_id"]).state == "cancelled"
        await observed(lambda: dead(child) and dead(parent))
        assert deps.registry.receipt(job["job_id"]).descendants_exited is True
        assert deps.registry.get_job(untouched.job_id).state == "queued"


@pytest.mark.parametrize("tool,args", [("github_thread_read", {"repository": REPO, "number": 7}),
    ("github_watch", {"threads": [{"repository": REPO, "number": 7}], "timeout_seconds": 1, "idempotency_key": "missing"}),
    ("prloop_next", {}), ("prloop_refresh", {"snapshot_id": "0" * 64, "idempotency_key": "missing"}),
    ("test_requests_read", {}), ("team_read", {"room": "room-a"}), ("nightly_status", {}), ("knowledge_search", {"query": "neutral"})])
async def test_knowledge_unavailable_components(kb, tool, args):
    settings = KnowledgeConfig(repositories=(REPO,), rooms=("room-a", "requests"), request_room="requests")
    cfg = replace(kb.config, knowledge=settings, checkout=None)
    async with Client(build_server(cfg)) as client:
        await error(client, tool, args, "unavailable")


@pytest.mark.parametrize("tool,args,protected", [("github_thread_read", {"repository": REPO, "number": 7}, "bin"),
    ("github_watch", {"threads": [{"repository": REPO, "number": 7}], "timeout_seconds": 1, "idempotency_key": "protected"}, "bin"),
    ("prloop_next", {}, "loop"), ("prloop_refresh", {"snapshot_id": "0" * 64, "idempotency_key": "protected"}, "loop"),
    ("test_requests_read", {}, "checkout"), ("team_read", {"room": "room-a"}, "checkout"),
    ("nightly_status", {}, "nightly"), ("knowledge_search", {"query": "neutral"}, "reports")])
async def test_knowledge_protected_paths(kb, tool, args, protected):
    cfg = replace(kb.config, protected_roots=(kb.root / protected,))
    async with Client(build_server(cfg)) as client:
        await error(client, tool, args, "refused_by_policy")


async def test_knowledge_sanitized_backend_failures(kb):
    fixture = json.loads((kb.bin / "backend.json").read_text())
    fixture["error"] = True
    put(kb.bin / "backend.json", fixture)
    loop = json.loads((kb.loop / "config.json").read_text())
    loop["error"] = True
    put(kb.loop / "config.json", loop)
    async with Client(build_server(kb.config)) as client:
        await error(client, "github_thread_read", {"repository": REPO, "number": 7}, "backend_failed")
        await error(client, "prloop_next", {}, "backend_failed")


async def test_knowledge_symlink_escape_and_line_bounds(kb):
    deps = Deps(kb.config)
    outside = kb.root / "other.md"
    outside.write_text("must not appear")
    (kb.reports / "alpha.md").unlink()
    (kb.reports / "alpha.md").symlink_to(outside)
    with pytest.raises(WorkbenchError) as failure:
        section_impl(deps, "reports", "alpha")
    assert failure.value.envelope.code == "refused_by_policy"
    with pytest.raises(WorkbenchError):
        section_impl(deps, "skills", "../other")
    with pytest.raises(WorkbenchError):
        section_impl(deps, "reports", "posted", limit=101)
    (kb.reports / "alpha.md").unlink()
    (kb.reports / "alpha.md").write_text("a" * 50000 + "\n")
    result = section_impl(deps, "reports", "alpha")
    assert result.truncated and result.lines[0]["content"]["truncated"]
    assert len(result.lines[0]["content"]["text"]) == 2048


def test_knowledge_operator_config_loader(kb):
    path = kb.root / "operator.json"
    path.write_text(kb.settings.model_dump_json())
    assert KnowledgeConfig.from_file(path) == kb.settings
    path.write_text('{"unknown": "private /private/credential"}')
    with pytest.raises(ValueError, match="^Invalid knowledge configuration$"):
        KnowledgeConfig.from_file(path)
    for value in ("../outside.md", "/outside.md", "dump.bin"):
        with pytest.raises(ValueError):
            ReportRegistration(id="bad", file=value)


async def test_knowledge_page_failure_is_explicit(kb):
    value = json.loads((kb.bin / "backend.json").read_text())
    del value["pages"][f"repos/{REPO}/pulls/7/reviews?per_page=100&page=1"]
    put(kb.bin / "backend.json", value)
    async with Client(build_server(kb.config)) as client:
        result = data(await client.call_tool("github_thread_read", {"repository": REPO, "number": 7}))
        assert not result["pages_complete"] and "reviews_read_incomplete" in result["warnings"]


@pytest.mark.parametrize("name,args", [("team_read", {"room": "room-a"}), ("test_requests_read", {})])
async def test_knowledge_missing_existing_signin(kb, name, args):
    value = json.loads((kb.scripts / "backend.json").read_text())
    value["unavailable"] = True
    put(kb.scripts / "backend.json", value)
    async with Client(build_server(kb.config)) as client:
        await error(client, name, args, "unavailable")


async def test_knowledge_command_bounds_and_deadline(kb):
    value = json.loads((kb.bin / "backend.json").read_text())
    value["overflow"] = True
    put(kb.bin / "backend.json", value)
    async with Client(build_server(kb.config)) as client:
        await error(client, "github_thread_read", {"repository": REPO, "number": 7}, "backend_failed")
    value.pop("overflow")
    value["block"] = True
    put(kb.bin / "backend.json", value)
    cfg = replace(kb.config, knowledge=kb.settings.model_copy(update={"read_timeout_seconds": 0.2}))
    async with Client(build_server(cfg)) as client:
        await error(client, "github_thread_read", {"repository": REPO, "number": 7}, "timeout")
    child = int((kb.bin / "child.pid").read_text())
    parent = int((kb.bin / "worker.pid").read_text())
    await observed(lambda: dead(child) and dead(parent))


async def test_knowledge_refresh_uncertain_outcome_not_replayed(kb):
    value = json.loads((kb.loop / "config.json").read_text())
    value["after_write_error"] = True
    put(kb.loop / "config.json", value)
    deps = Deps(kb.config)
    async with Client(build_server(deps=deps)) as client:
        args = {"snapshot_id": data(await client.call_tool("prloop_next"))["snapshot_id"], "idempotency_key": "uncertain"}
        job = data(await client.call_tool("prloop_refresh", args))
        await observed(lambda: deps.registry.get_job(job["job_id"]).state in TERMINAL)
        result = deps.registry.get_job(job["job_id"])
        assert result.state == "failed" and result.error.code == "uncertain_outcome"
        assert "private" not in result.model_dump_json()
        again = data(await client.call_tool("prloop_refresh", args))
        assert again["job_id"] == job["job_id"] and again["error"]["code"] == "uncertain_outcome"
        assert (kb.loop / "scans").read_text().splitlines() == ["scan"]


async def test_knowledge_refresh_untrusted_checkout_refused(kb):
    value = json.loads((kb.loop / "config.json").read_text())
    value["checkout"] = str(kb.root / "untrusted")
    put(kb.loop / "config.json", value)
    async with Client(build_server(kb.config)) as client:
        snapshot = data(await client.call_tool("prloop_next"))["snapshot_id"]
        await error(client, "prloop_refresh", {"snapshot_id": snapshot, "idempotency_key": "foreign"}, "refused_by_policy")
    assert not (kb.loop / "scans").exists()


async def test_knowledge_watch_foreign_cursor_refused(kb):
    from src.models import Producer
    deps = Deps(kb.config)
    async with Client(build_server(deps=deps)) as client:
        path = kb.root / "unrelated.json"
        put(path, {})
        artifact = deps.registry.add_artifact(path, guards=deps.paths, mime="application/json", producer=Producer(tool="other"), complete=True)
        arguments = {"threads": [{"repository": REPO, "number": 7}], "timeout_seconds": 1, "idempotency_key": "foreign", "prior_cursor": artifact.id}
        await error(client, "github_watch", arguments, "refused_by_policy")
        arguments.pop("prior_cursor")
        arguments["threads"][0]["repository"] = "outside/project"
        await error(client, "github_watch", arguments, "refused_by_policy")


async def test_knowledge_index_entry_budget(kb):
    (kb.reports / "alpha.md").write_text("\n".join("## Synthetic heading " + str(n) for n in range(10020)))
    async with Client(build_server(kb.config)) as client:
        result = data(await client.call_tool("knowledge_search", {"query": "Synthetic", "collection": "reports", "limit": 2}))
        assert len(result["matches"]) == 2 and result["next_cursor"]
        assert "index_entry_budget_exhausted" in result["stale_warnings"]


async def test_knowledge_nightly_missing_schema_stays_read_only(kb):
    path = kb.root / "empty.sqlite"
    db = sqlite3.connect(path)
    db.close()
    before = path.read_bytes()
    cfg = replace(kb.config, knowledge=kb.settings.model_copy(update={"nightly_database": path}))
    async with Client(build_server(cfg)) as client:
        result = data(await client.call_tool("nightly_status"))
        assert result["last_complete"] is None and any("database observation unavailable" in s for s in result["limitations"])
    assert path.read_bytes() == before
