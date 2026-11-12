import asyncio
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import pytest
from fastmcp import Client
from src.config import Config
from src.deps import Deps
from src.effects.checks import build_plan_impl
from src.effects.checks_common import dry_run, parse_pr, parse_source, parse_windows, safe_text
from src.main import build_server
from src.models import Producer, State, TERMINAL
from src.registry.process import identity, same_process
from src.tools import registry
from src.tools.checks import SPECS


@dataclass(frozen=True)
class ChecksConfig(Config):
    checks_catalogue: Path | None = None


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-C", str(root), *args],
                                   stderr=subprocess.DEVNULL, text=True).strip()


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "GROUPS", ("status", "checks"))
    root = tmp_path / "checkout"
    root.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"pids": str(tmp_path / "pids.json")}))
    fixture = (Path(__file__).parent / "fakes/checks_backend.py").read_text().replace("@CONTROL@", str(control))
    def program(path):
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("#!" + sys.executable + "\n" + fixture)
        path.chmod(0o700)
    for name in ("tests/run_all.py", "nx2/build.py", "nx2/make_game.py"):
        program(root / name)
    for name in ("alpha", "beta", "skip", "ready"):
        (root / "tests" / ("test_" + name + ".py")).write_text("# neutral test\n")
    (root / "host").mkdir()
    (root / "host/runtime.c").write_text("void imp_fixture(Ctx *ctx) {}\n")
    (root / "base_case.txt").write_text("base\n")
    git(root, "init", "-b", "candidate")
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "base_case.txt").write_text("head\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "candidate")
    head = git(root, "rev-parse", "HEAD")
    for name in ("idle", "nocore", "build-gate", "windows", "preflight", "gh", "copy"):
        program(tools / name)
    parser = tools / "ci_reader.py"
    parser.write_text("import re\ndef failures(text):\n return {'tests': re.findall(r'FAIL\\s+[\\d.]+s\\s+(tests/[^\\s]+)', text), 'cases': []}\n")
    for name in ("cpm", "sysroot", "exefs", "romfs"):
        (tmp_path / name).mkdir()
    (tmp_path / "exefs/input.dat").write_text("neutral fixture input")
    catalogue = tmp_path / "catalogue.json"
    data = {"checkouts": {"fixture": {"path": str(root), "reviewed_commits": [base, head]}},
        "profiles": {"default": {"cpm": str(tmp_path / "cpm"), "toolchain_identity": "1" * 64,
            "exefs": str(tmp_path / "exefs"), "romfs": str(tmp_path / "romfs"), "wall_seconds": 30, "queue_seconds": 30}},
        "git": shutil.which("git"), "copy_program": str(tools / "copy"), "flock": shutil.which("flock"),
        "idle": str(tools / "idle"), "nocore": str(tools / "nocore"), "full_build_lock": str(tmp_path / "full.lock"),
        "windows_check": str(tools / "windows"), "windows_sysroot": str(tmp_path / "sysroot"),
        "pr_check": str(tools / "preflight"), "pr_check_checkout": str(root),
        "ci_reader": str(parser), "gh": str(tools / "gh"), "repositories": ["Example/Project"]}
    catalogue.write_text(json.dumps(data))
    config = ChecksConfig(state_root=tmp_path / "state", read_roots=(tmp_path, Path(sys.executable).resolve().parent, Path("/usr/bin")),
        write_roots=(tmp_path,), protected_roots=(tmp_path / "protected",), profile="operate", checks_catalogue=catalogue,
        build_gate=tools / "build-gate", dependency_python=Path(sys.executable).resolve())
    config.state_root.mkdir()
    rule = {"lanes": ["build", "idle"], "classes": ["ordinary"], "max_hold_seconds": 480}
    (config.state_root / "policy.json").write_text(json.dumps({"day": rule, "night": rule}))
    deps = Deps(config)
    return SimpleNamespace(root=root, config=config, deps=deps, data=data, catalogue=catalogue, control=control,
                           base=base, head=head, tmp=tmp_path, tools=tools)


def set_control(bench, **values):
    bench.control.write_text(json.dumps({**json.loads(bench.control.read_text()), **values}))


def save_catalogue(bench):
    bench.catalogue.write_text(json.dumps(bench.data))


def wire(result):
    return result.structured_content


async def call(client, name, **kwargs):
    return wire(await client.call_tool(name, kwargs))


async def finished(client, job_id):
    data = await call(client, "job_wait", job_id=job_id, timeout_seconds=15)
    assert data["terminal"], data
    return data


def result_for(bench, job_id):
    job = bench.deps.registry.get_job(job_id)
    for uri in reversed(job.artifact_uris):
        aid = uri.rsplit("/", 1)[1]
        path = bench.deps.registry.artifact_path(aid, bench.deps.paths)
        if path.name == "result.json":
            return json.loads(path.read_text())
    raise AssertionError("missing result artifact")


def title(bench):
    path = bench.tmp / "title.txt"
    path.write_text("fix(core): neutral fixture\n")
    return bench.deps.registry.add_artifact(path, guards=bench.deps.paths, mime="text/plain", producer=Producer(tool="fixture"), complete=True).id


async def approved_plan(client, bench, **kwargs):
    result = await call(client, "build_plan", repo_id="fixture", commit=bench.head, profile_id="default", **kwargs)
    bench.data.setdefault("approved_plans", []).append(result["plan"]["plan_hash"])
    save_catalogue(bench)
    return result


@pytest.mark.parametrize("profile,extras", [("observe", ()), ("operate", ()), ("observe", ("checks_source",))])
async def test_checks_catalogue(bench, profile, extras):
    deps = Deps(replace(bench.config, profile=profile, extra_tools=extras))
    required = {"build_plan": ["repo_id", "commit", "profile_id"], "build_start": ["plan_id", "idempotency_key"],
        "checks_source": ["repo_id", "commit", "idempotency_key"], "checks_windows_syntax": ["repo_id", "commit", "base", "idempotency_key"],
        "checks_pr": ["repo_id", "commit", "branch", "title_artifact", "idempotency_key"], "ci_read": ["repository"]}
    async with Client(build_server(deps=deps)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        expected = {s.name for s in SPECS if profile == "operate" or s.name == "ci_read" or s.name in extras}
        assert set(tools).intersection(required) == expected
        for spec in SPECS:
            if spec.name not in expected:
                continue
            tool = tools[spec.name]
            assert tool.annotations.read_only_hint == (spec.name == "ci_read")
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint == (spec.name != "build_plan")
            assert tool.annotations.open_world_hint == (spec.name in {"ci_read", "checks_pr"})
            assert tool.input_schema.get("required", []) == required[spec.name]
        assert not {"fork_ci_start", "git_push", "shell", "python_eval"}.intersection(tools)


async def test_source_and_base_only_failing_names(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        args = dict(repo_id="fixture", commit=bench.head, compare_with_base=bench.base, idempotency_key="source")
        started = time.monotonic()
        job = await call(client, "checks_source", **args)
        assert time.monotonic() - started < 3
        duplicate = await call(client, "checks_source", **args)
        assert duplicate["job_id"] == job["job_id"]
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "failed"
        assert done["receipt"]["descendants_exited"] is True
        result = result_for(bench, job["job_id"])
        assert (result["passed"], result["failed"], result["skipped"]) == (0, 2, 1)
        statuses = {t["name"]: t["also_fails_on_base"] for t in result["tests"]}
        assert statuses == {"tests/test_alpha.py": True, "tests/test_beta.py": False, "tests/test_skip.py": None}
        base_log = bench.config.state_root / "checks" / (job["job_id"] + "-output/base.log")
        assert "test_skip.py" not in base_log.read_text()
        events = await call(client, "job_read", job_id=job["job_id"], limit=100)
        assert any(e["kind"] == "check_row" for e in events["events"]["events"])
        assert any(e["kind"] == "queue" for e in events["events"]["events"])


async def test_source_named_and_result_cursor(bench):
    set_control(bench, many=True)
    async with Client(build_server(deps=bench.deps)) as client:
        job = await call(client, "checks_source", repo_id="fixture", commit=bench.head, idempotency_key="many")
        await finished(client, job["job_id"])
        result = result_for(bench, job["job_id"])
        assert result["passed"] == 120 and len(result["tests"]) == 100 and result["truncated"]
        cursor, rows = 0, []
        while True:
            page = (await call(client, "job_read", job_id=job["job_id"], cursor=cursor, limit=7))["events"]
            rows.extend(e for e in page["events"] if e["kind"] == "check_row")
            if not page["has_more"]:
                break
            assert page["next_cursor"] > cursor
            cursor = page["next_cursor"]
        assert len(rows) == 120


async def test_windows_syntax_never_runtime(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        job = await call(client, "checks_windows_syntax", repo_id="fixture", commit=bench.head, base=bench.base,
                         fixtures=["tests/test_alpha.py"], idempotency_key="windows")
        await finished(client, job["job_id"])
        result = result_for(bench, job["job_id"])
        assert result["windows_runtime"] == "not_run"
        assert (result["compiled"], result["failed"], result["unjudged"]) == (1, 0, 1)
        assert "/private" not in json.dumps(result)


async def test_pr_fetch_and_imported_pairs(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        job = await call(client, "checks_pr", repo_id="fixture", commit=bench.head, branch="candidate", title_artifact=title(bench),
                         imported=[{"commit": bench.head, "original": bench.base}], idempotency_key="preflight")
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "completed"
        result = result_for(bench, job["job_id"])
        assert result["pass"] and result["fetch_status"] == "completed"
        assert "/private" not in json.dumps(result) and "hidden" not in json.dumps(result)


async def test_build_plan_start_reuse_and_mismatch(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        plan = await approved_plan(client, bench)
        assert plan["plan"]["mode"] == "full"
        assert plan["plan"]["required_gates"] == ["build", "full_build_lock"]
        assert plan["plan"]["estimated_time"] is None
        assert not bench.deps.registry.list_jobs().jobs
        job = await call(client, "build_start", plan_id=plan["artifact"]["id"], idempotency_key="full")
        duplicate = await call(client, "build_start", plan_id=plan["artifact"]["id"], idempotency_key="full")
        assert job["job_id"] == duplicate["job_id"]
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "completed", done
        run = bench.deps.registry.get_run(job["run_ids"][0])
        assert run.state == State.COMPLETED
        assert run.build.commit == bench.head and run.build.source_stamp == "abc123"
        assert set(run.build.binary_hashes) == {"hookshot", "hookshot_vk.so"}
        phases = {e.data.get("phase") for e in bench.deps.registry.events(job["job_id"], limit=100).events}
        assert {"lift", "compile", "link", "finish"} <= phases
        reuse = await approved_plan(client, bench, donor_id=run.run_id)
        assert reuse["plan"]["mode"] == "reuse", reuse
        reused = await call(client, "build_start", plan_id=reuse["artifact"]["id"], idempotency_key="reuse")
        await finished(client, reused["job_id"])
        assert result_for(bench, reused["job_id"])["completed_receipt"]["kind"] == "linked_outputs"
        (bench.root / "host/runtime.c").write_text("void imp_fixture(Ctx *ctx) {} /* host-only change */\n")
        git(bench.root, "add", ".")
        git(bench.root, "commit", "-m", "host-only change")
        host_head = git(bench.root, "rev-parse", "HEAD")
        bench.data["checkouts"]["fixture"]["reviewed_commits"].append(host_head)
        save_catalogue(bench)
        host_plan = await call(client, "build_plan", repo_id="fixture", commit=host_head, profile_id="default", donor_id=run.run_id)
        assert host_plan["plan"]["mode"] == "reuse"
        (bench.root / "host/runtime.c").write_text("void imp_new_fixture(Ctx *ctx) {}\n")
        git(bench.root, "add", ".")
        git(bench.root, "commit", "-m", "translated hook change")
        hook_head = git(bench.root, "rev-parse", "HEAD")
        bench.data["checkouts"]["fixture"]["reviewed_commits"].append(hook_head)
        save_catalogue(bench)
        hook_plan = await call(client, "build_plan", repo_id="fixture", commit=hook_head, profile_id="default", donor_id=run.run_id)
        assert hook_plan["plan"]["mode"] == "full"
        set_control(bench, dry_missing=1)
        mismatch = await call(client, "build_plan", repo_id="fixture", commit=bench.head, profile_id="default", donor_id=run.run_id)
        assert mismatch["plan"]["mode"] == "full"


async def test_build_requires_done_receipt(bench):
    set_control(bench, no_receipt=True)
    async with Client(build_server(deps=bench.deps)) as client:
        plan = await approved_plan(client, bench)
        job = await call(client, "build_start", plan_id=plan["artifact"]["id"], idempotency_key="missing")
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "failed"
        assert done["job"]["error"]["code"] == "backend_failed"
        assert bench.deps.registry.get_run(job["run_ids"][0]).state == State.FAILED


@pytest.mark.parametrize("operation,role", [("checks_source", "source"), ("checks_windows_syntax", "windows"), ("checks_pr", "pr"), ("build_start", "build")])
async def test_each_long_job_cancels_owned_descendants_and_deduplicates(bench, operation, role):
    set_control(bench, block=role)
    async with Client(build_server(deps=bench.deps)) as client:
        args = dict(repo_id="fixture", commit=bench.head, idempotency_key="cancel")
        if operation == "checks_windows_syntax":
            args["base"] = bench.base
        elif operation == "checks_pr":
            args.update(branch="candidate", title_artifact=title(bench))
        elif operation == "build_start":
            plan = await approved_plan(client, bench)
            args = dict(plan_id=plan["artifact"]["id"], idempotency_key="cancel")
        job = await call(client, operation, **args)
        assert (await call(client, operation, **args))["job_id"] == job["job_id"]
        deadline = time.monotonic() + 10
        path = bench.tmp / "pids.json"
        while not path.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert path.exists()
        births = [identity(pid) for pid in json.loads(path.read_text())]
        assert all(births)
        if operation == "build_start":
            busy = await client.call_tool(operation, {**args, "idempotency_key": "second"}, raise_on_error=False)
            assert json.loads(busy.content[0].text)["code"] == "busy"
        await call(client, "job_cancel", job_id=job["job_id"])
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "cancelled"
        assert done["receipt"]["process_exited"] and done["receipt"]["descendants_exited"]
        assert all(not same_process(birth) for birth in births)
        events = (await call(client, "job_read", job_id=job["job_id"], limit=100))["events"]["events"]
        assert any(e["kind"] == "admitted" for e in events)
        assert (await call(client, operation, **args))["job_id"] == job["job_id"]


async def test_queued_admission_policy_rechecked(bench):
    set_control(bench, queue=True)
    async with Client(build_server(deps=bench.deps)) as client:
        job = await call(client, "checks_source", repo_id="fixture", commit=bench.head, idempotency_key="queue")
        assert job["started_at"] is None
        (bench.config.state_root / "policy.json").write_text("{}")
        set_control(bench, queue=False)
        done = await finished(client, job["job_id"])
        assert done["job"]["state"] == "failed"
        assert done["job"]["error"]["code"] == "refused_by_policy"
        assert not (bench.tmp / "pids.json").exists()


@pytest.mark.parametrize("target", ["pull", "run"])
async def test_ci_heads_checks_names_main_and_cursor(bench, target):
    async with Client(build_server(deps=bench.deps)) as client:
        args = {"repository": "Example/Project", "pull_request" if target == "pull" else "run_id": 7 if target == "pull" else 11}
        first = await call(client, "ci_read", **args, limit=1)
        assert first["head"] == "a" * 40
        assert first["checks"][0]["failed_tests"] == [{"name": "tests/test_alpha.py", "also_red_on_main": "yes", "main_head": "b" * 40}]
        assert first["next_cursor"] == 1
        second = await call(client, "ci_read", **args, cursor=first["next_cursor"], limit=1)
        assert second["checks"][0]["conclusion"].lower() == "success" and second["next_cursor"] is None
        if target == "pull":
            assert first["review_state"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("setting", ["no_main", "log_error"])
async def test_ci_unknown_not_green(bench, setting):
    set_control(bench, **{setting: True})
    async with Client(build_server(deps=bench.deps)) as client:
        result = await call(client, "ci_read", repository="Example/Project", run_id=11)
        assert result["checks"][0]["unknown"]
        assert "secret" not in json.dumps(result) and "/private" not in json.dumps(result)
        if setting == "no_main":
            assert result["checks"][0]["failed_tests"][0]["also_red_on_main"] == "unknown"
            assert result["unknown"] == ["no_completed_main_run"]


@pytest.mark.parametrize("kind", ["policy", "protected", "unavailable", "review", "unknown_repo", "title", "pr_binding", "approval"])
async def test_refusals_are_sanitized(bench, kind):
    async with Client(build_server(deps=bench.deps)) as client:
        operation = "checks_source"
        args = dict(repo_id="fixture", commit=bench.head, idempotency_key="refused")
        if kind == "policy":
            (bench.config.state_root / "policy.json").write_text("{}")
        elif kind == "protected":
            bench.data["checkouts"]["fixture"]["path"] = str(bench.tmp / "protected")
            (bench.tmp / "protected").mkdir()
            save_catalogue(bench)
        elif kind == "unavailable":
            bench.data["idle"] = None
            save_catalogue(bench)
        elif kind == "review":
            bench.data["checkouts"]["fixture"]["reviewed_commits"] = []
            save_catalogue(bench)
        elif kind == "unknown_repo":
            operation, args = "ci_read", {"repository": "Other/Project", "run_id": 11}
        elif kind in ("title", "pr_binding"):
            operation = "checks_pr"
            aid = title(bench)
            args.update(branch="candidate", title_artifact=aid)
            if kind == "title":
                (bench.tmp / "title.txt").write_text("changed")
            else:
                bench.data["pr_check_checkout"] = None
                save_catalogue(bench)
        elif kind == "approval":
            plan = await call(client, "build_plan", repo_id="fixture", commit=bench.head, profile_id="default")
            operation, args = "build_start", {"plan_id": plan["artifact"]["id"], "idempotency_key": "unapproved"}
        result = await client.call_tool(operation, args, raise_on_error=False)
        assert result.is_error
        error = json.loads(result.content[0].text)
        assert set(error) == {"code", "operation", "retryable", "retry_after_seconds", "operator_action"}
        assert error["code"] in {"refused_by_policy", "unavailable"}
        assert str(bench.tmp) not in result.content[0].text
        assert not bench.deps.registry.list_jobs().jobs


@pytest.mark.parametrize("operation,args", [
    ("checks_source", {"repo_id": "fixture", "commit": "a" * 40, "idempotency_key": "x", "tests": ["../bad"]}),
    ("checks_source", {"repo_id": "fixture", "commit": "a" * 40, "idempotency_key": "x", "workers": 9}),
    ("ci_read", {"repository": "Example/Project", "run_id": 11, "limit": 101}),
    ("ci_read", {"repository": "Example/Project", "run_id": 11, "pull_request": 7}),
    ("ci_read", {"repository": "Example/Project", "cursor": -1}),
])
async def test_input_bounds(bench, operation, args):
    async with Client(build_server(deps=bench.deps)) as client:
        result = await client.call_tool(operation, args, raise_on_error=False)
        assert result.is_error and json.loads(result.content[0].text)["code"] == "invalid_argument"


def test_parsers_keep_missing_unknown_and_table_authoritative():
    text = "FAIL 1.0s tests/test_a.py\nresult time check\npass 0.1s tests/test_a.py\nskip tests/test_b.py (unavailable)\n1 passed, 0 failed, 1 skipped in 1 s\n"
    rows, counts, complete = parse_source(text)
    assert complete and counts == (1, 0, 1) and rows[0].status == "passed" and rows[1].seconds is None
    assert parse_source("nothing reported")[1] == (None, None, None)
    assert parse_source(text.replace("1 passed", "2 passed"))[2] is False
    assert dry_run("unknown")["missing"] is None
    assert parse_pr("WARN could not fetch origin\nFAIL\n", 1).fetch_status == "failed"
    assert parse_pr("", 0).passed is None
    assert safe_text("token=private /some/local/path") == "[redacted] [local-path]"


@pytest.mark.parametrize("operation,missing", [
    ("build_plan", "full_build_lock"), ("build_start", "copy_program"),
    ("checks_source", "nocore"), ("checks_windows_syntax", "windows_sysroot"),
    ("checks_pr", "pr_check"), ("ci_read", "ci_reader"),
])
async def test_each_backend_prerequisite_fails_closed(bench, operation, missing):
    async with Client(build_server(deps=bench.deps)) as client:
        args = dict(repo_id="fixture", commit=bench.head, idempotency_key="missing")
        if operation == "build_plan":
            args = dict(repo_id="fixture", commit=bench.head, profile_id="default")
        elif operation == "build_start":
            plan = await approved_plan(client, bench)
            args = dict(plan_id=plan["artifact"]["id"], idempotency_key="missing")
        elif operation == "checks_windows_syntax":
            args["base"] = bench.base
        elif operation == "checks_pr":
            args.update(branch="candidate", title_artifact=title(bench))
        elif operation == "ci_read":
            args = dict(repository="Example/Project", run_id=11)
        if missing == "copy_program":
            # Required executable paths are validated before starting a worker.
            bench.data[missing] = str(bench.tmp / "absent-copy")
        else:
            bench.data[missing] = None
        save_catalogue(bench)
        result = await client.call_tool(operation, args, raise_on_error=False)
        assert result.is_error
        assert json.loads(result.content[0].text)["code"] in {"unavailable", "not_found"}
        assert str(bench.tmp) not in result.content[0].text
        assert not bench.deps.registry.list_jobs().jobs


async def test_source_exact_selection_and_key_conflict(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        args = dict(repo_id="fixture", commit=bench.head, idempotency_key="selected", tests=["tests/test_alpha.py"])
        job = await call(client, "checks_source", **args)
        await finished(client, job["job_id"])
        result = result_for(bench, job["job_id"])
        assert result["coverage"] == "named" and result["failing_names"] == ["tests/test_alpha.py"]
        conflict = await client.call_tool("checks_source", {**args, "tests": ["tests/test_beta.py"]}, raise_on_error=False)
        assert json.loads(conflict.content[0].text)["code"] == "invalid_argument"


async def test_ci_protected_backend_and_missing_catalogue(bench):
    async with Client(build_server(deps=bench.deps)) as client:
        protected = bench.tmp / "protected"
        protected.mkdir()
        parser = protected / "parser.py"
        parser.write_text("raise AssertionError('must never import')")
        bench.data["ci_reader"] = str(parser)
        save_catalogue(bench)
        result = await client.call_tool("ci_read", {"repository": "Example/Project", "run_id": 11}, raise_on_error=False)
        assert json.loads(result.content[0].text)["code"] == "refused_by_policy"
    deps = Deps(replace(bench.config, checks_catalogue=None))
    async with Client(build_server(deps=deps)) as client:
        result = await client.call_tool("ci_read", {"repository": "Example/Project", "run_id": 11}, raise_on_error=False)
        assert json.loads(result.content[0].text)["code"] == "unavailable"


async def test_catalogue_symlink_and_output_symlink_are_refused(bench):
    real = bench.tmp / "real-catalogue.json"
    bench.catalogue.rename(real)
    bench.catalogue.symlink_to(real)
    async with Client(build_server(deps=bench.deps)) as client:
        result = await client.call_tool("ci_read", {"repository": "Example/Project", "run_id": 11}, raise_on_error=False)
        assert json.loads(result.content[0].text)["code"] == "refused_by_policy"


def test_bounded_capture_retains_fixed_error(bench):
    from src.effects.checks_common import capture
    from src.errors import WorkbenchError
    with pytest.raises(WorkbenchError) as failure:
        capture([sys.executable, "-c", "print('x' * 1024)"], cwd=bench.tmp, limit=64)
    assert failure.value.envelope.code == "backend_failed"
    assert str(bench.tmp) not in str(failure.value)


async def test_real_check_outputs_feed_registered_evidence_bundle(bench, monkeypatch):
    monkeypatch.setattr(registry, "GROUPS", ("status", "checks", "evidence"))
    async with Client(build_server(deps=bench.deps)) as client:
        plan = await approved_plan(client, bench)
        built = await call(client, "build_start", plan_id=plan["artifact"]["id"], idempotency_key="bundle-build")
        assert (await finished(client, built["job_id"]))["job"]["state"] == "completed"
        build_job = bench.deps.registry.get_job(built["job_id"])
        build_artifact = next(uri.rsplit("/", 1)[-1] for uri in build_job.artifact_uris
            if bench.deps.registry.artifact_path(uri.rsplit("/", 1)[-1], bench.deps.paths).name == "build-manifest.json")
        set_control(bench, many=True)
        checked = await call(client, "checks_source", repo_id="fixture", commit=bench.head, idempotency_key="bundle-suite")
        assert (await finished(client, checked["job_id"]))["job"]["state"] == "completed"
        result = await call(client, "evidence_bundle", request={
            "base": bench.base, "head": bench.head, "topic": "Synthetic integration", "rule": "source",
            "build_artifact_ids": [build_artifact], "check_ids": [checked["job_id"]]})
        assert "completed_build_receipt_for_head" not in result["missing_requirements"]
        assert "full_source_suite_on_exact_head" not in result["missing_requirements"]
        assert result["publication"] == "not_published"
