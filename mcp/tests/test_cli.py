"""Real CLI/MCP/broker integration using only temporary synthetic backends."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import pytest
from fastmcp import Client

from src import runtime
from src.broker import paths
from src.config import Config
from src.deps import Deps
from src.effects.knowledge_prompts import PLANS
from src.errors import fail
from src.main import build_server
from src.models import ErrorEnvelope, Job, TERMINAL
from src.registry import Registry
from src.registry.process import identity


PROJECT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).parent / "fakes"
STARTED = "hookshot-wb: broker started\n"


class Shell:
    def __init__(self, root):
        self.root = root
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("HOOKSHOT_WB_")}
        for key in ("PYTHONPATH", "PYTHONSTARTUP"):
            self.env.pop(key, None)
        self.env.update({
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_STATE_HOME": str(root / "xdg-state"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "HOOKSHOT_WB_STATE_ROOT": str(root / "state"),
            "HOOKSHOT_WB_READ_ROOTS": json.dumps([str(root)]),
            "HOOKSHOT_WB_WRITE_ROOTS": json.dumps([str(root)]),
            "HOOKSHOT_WB_DEPENDENCY_PYTHON": sys.executable,
        })
        (root / "home").mkdir()

    @property
    def config(self):
        return Config.from_env(self.env)

    def __call__(self, *arguments, timeout=25):
        return subprocess.run([sys.executable, "-B", "-m", "src.cli", *arguments],
                              cwd=PROJECT, env=self.env, capture_output=True, text=True, timeout=timeout)


def success(result, *, started=False):
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == (STARTED if started else "")
    return json.loads(result.stdout)


def failure(result, code, *, started=False):
    assert result.returncode != 0
    assert result.stdout == ""
    diagnostic = result.stderr
    if started:
        assert diagnostic.startswith(STARTED)
        diagnostic = diagnostic[len(STARTED):]
    envelope = ErrorEnvelope.model_validate_json(diagnostic)
    assert envelope.code == code
    assert "Traceback" not in diagnostic and "/private" not in diagnostic and "wbcli-" not in diagnostic
    assert diagnostic.count("\n") == 1
    return envelope.model_dump()


def observed(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Synthetic fixture did not reach the requested state"
        time.sleep(0.02)


@pytest.fixture
def shell():
    # Session bridges have a Unix socket path bound; keep fixtures independent
    # of pytest's potentially long working-directory names.
    with tempfile.TemporaryDirectory(prefix="wbcli-") as folder:
        cli = Shell(Path(folder))
        socket = paths(cli.config).socket
        try:
            yield cli
        finally:
            if socket.exists():
                success(cli("broker", "stop", "--timeout", "40", timeout=50))


@pytest.fixture
def knowledge(shell):
    name = "recover_or_cancel"
    registrations = {}
    for skill, heading in PLANS[name]:
        registrations.setdefault(skill, []).append(heading)
    skills = shell.root / "skills"
    for skill, headings in registrations.items():
        folder = skills / skill
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("# Synthetic CLI skill\n" + "\n".join(
            f"## {heading}\nSynthetic CLI guidance for {heading}.\n" for heading in headings))
    settings = shell.root / "knowledge.json"
    settings.write_text(json.dumps({"skills_dir": str(skills), "skill_names": list(registrations)}))
    shell.env["HOOKSHOT_WB_KNOWLEDGE_CONFIG"] = str(settings)
    return shell


@pytest.fixture
def session(shell):
    root = shell.root
    for name in ("state", "build", "harness", "clones"):
        (root / name).mkdir()
    (root / "build/romfs").mkdir()
    (root / "build/image.bin").write_bytes(b"synthetic image, not game data")
    executable = root / "build/hookshot"
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(99)\n")
    executable.chmod(0o700)
    rule = {"lanes": ["gpu"], "classes": ["ordinary"]}
    (root / "state/policy.json").write_text(json.dumps({"day": rule, "night": rule}))
    fake = FAKES / "session_process.py"
    for name, mode in (("gate", "gate"), ("harness/hs-run.sh", "wrapper")):
        executable = root / name
        executable.write_text(f"#!{sys.executable}\nimport os, sys\n"
            f"os.environ['SESSION_FIXTURE'] = {str(root)!r}\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, '-I', {str(fake)!r}, {mode!r}, *sys.argv[1:]])\n")
        executable.chmod(0o700)
    (root / "gate-delay").write_text("0")
    (root / "quit-mode").write_text("reply")
    catalogue = root / "sessions.json"
    catalogue.write_text(json.dumps({
        "builds": {"synthetic": {"path": str(root / "build"), "reviewed": True, "owned": True}},
        "profiles": {"ordinary": {"debug": True}},
        "clone_root": str(root / "clones"),
    }))
    shell.env.update({"HOOKSHOT_WB_HARNESS_DIR": str(root / "harness"),
                      "HOOKSHOT_WB_GPU_GATE": str(root / "gate"),
                      "HOOKSHOT_WB_SESSION_CATALOGUE": str(catalogue)})
    return shell


@pytest.mark.parametrize("profile", ["observe", "operate"])
async def test_json_catalogue_matches_real_registration(shell, profile):
    shell.env["HOOKSHOT_WB_PROFILE"] = profile
    deps = Deps(shell.config)
    async with Client(build_server(deps=deps)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    rows = success(shell("list", "--profile", profile, "--json"))
    assert {row["name"] for row in rows} == set(tools)
    required = {"name", "group", "safety", "profiles", "requires_broker", "shell_route", "description", "input_schema"}
    for row in rows:
        assert required <= row.keys()
        spec = deps.specs[row["name"]]
        assert row["group"] == deps.groups[row["name"]]
        assert row["safety"] == spec.safety.value
        assert row["description"] == spec.description
        assert row["input_schema"] == tools[row["name"]].input_schema
        assert row["requires_broker"] is spec.requires_broker
        assert row["shell_route"] == spec.shell_route and "\n" not in row["shell_route"]
        assert profile in row["profiles"]
    assert not paths(shell.config).socket.exists()
    assert not paths(shell.config).owner.exists()


def test_table_contains_broker_and_replacement_columns(shell):
    result = shell("list", "--profile", "operate")
    assert result.returncode == 0 and result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[0].split() == ["tool", "group", "safety", "profile", "broker", "replaces"]
    rows = {line.split()[0]: line for line in lines[1:]}
    assert "session_launch" in rows and "workbench_status" in rows
    for name in ("session_launch", "session_state", "workbench_status"):
        spec = runtime.tool_spec(Config.from_env({**shell.env, "HOOKSHOT_WB_PROFILE": "operate"}), name)
        assert rows[name].endswith(spec.shell_route)
        assert rows[name].split()[4] == ("yes" if spec.requires_broker else "no")
    assert not paths(shell.config).socket.exists()


def test_profile_override_preserves_other_configuration(shell):
    harness = shell.root / "registered-harness"
    harness.mkdir()
    shell.env.update({"HOOKSHOT_WB_PROFILE": "operate", "HOOKSHOT_WB_EXTRA_TOOLS": "job_cancel",
                      "HOOKSHOT_WB_HARNESS_DIR": str(harness)})
    rows = success(shell("--timeout", "10", "list", "--json", "--profile", "observe"))
    names = {row["name"] for row in rows}
    assert "job_cancel" in names and "session_launch" not in names
    status = success(shell("--profile", "observe", "call", "workbench_status", "--args", "{}"))
    assert status["capabilities"]["harness"]["status"] == "configured"
    failure(shell("call", "session_launch", "--profile", "observe", "--args", "{}"), "not_found")
    assert not paths(shell.config).socket.exists()


@pytest.mark.parametrize("name", ["workbench_status", "jobs_list", "queue_status"])
def test_simple_read_tools_do_not_start_a_broker(shell, name):
    value = success(shell("call", name, "--args", "{}"))
    assert isinstance(value, dict)
    assert not paths(shell.config).socket.exists()
    assert not paths(shell.config).owner.exists()


@pytest.mark.parametrize("value", ["", "{", "null", "[]", '"text"', "true", "1", "{} trailing",
                                    '{"limit": NaN}', '{"limit": Infinity}', '{"limit": 1e999}'])
def test_malformed_json_is_a_fixed_argument_error(shell, value):
    error = failure(shell("call", "jobs_list", "--args", value), "invalid_argument")
    assert error == fail("invalid_argument", "cli").envelope.model_dump()
    assert not paths(shell.config).socket.exists()


@pytest.mark.parametrize("arguments", [
    (), ("unknown-command", "/private/secret"), ("call",), ("broker",),
    ("list", "--profile", "/private/secret"), ("list", "--json", "--unknown", "/private/secret"),
    ("call", "jobs_list", "--timeout", "0"), ("list", "--timeout", "nan"),
    ("list", "--timeout", "inf"), ("serve", "--idle-seconds", "-1"),
    ("serve", "--lifetime-seconds", "0"), ("serve", "--lock-fd", "-1"),
    ("serve", "--config-fd", "not-a-descriptor"),
])
def test_parser_errors_never_echo_arguments(shell, arguments):
    error = failure(shell(*arguments), "invalid_argument")
    assert error == fail("invalid_argument", "cli").envelope.model_dump()
    assert not paths(shell.config).socket.exists()


@pytest.mark.parametrize("arguments", [{"limit": -1}, {"limit": "wrong"}, {"unadvertised": "/private/secret"}])
async def test_schema_errors_preserve_the_framework_envelope(shell, arguments):
    async with Client(build_server(shell.config)) as client:
        expected = await client.call_tool("jobs_list", arguments, raise_on_error=False)
    error = failure(shell("call", "jobs_list", "--args", json.dumps(arguments)), "invalid_argument")
    assert error == json.loads(expected.content[0].text)
    assert not paths(shell.config).socket.exists()


def test_unknown_tool_is_not_an_execution_interface(shell):
    marker = shell.root / "must-not-exist"
    name = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    failure(shell("call", name, "--args", "{}"), "not_found")
    assert not marker.exists() and not paths(shell.config).socket.exists()


async def test_registered_resource_is_passed_through(shell):
    config = Config.from_env({**shell.env, "HOOKSHOT_WB_PROFILE": "operate"})
    expected = await runtime.one_shot(config, "read", name="workbench://tools")
    assert "result" in expected
    value = success(shell("read", "workbench://tools", "--profile", "operate"))
    assert value == expected["result"]
    assert "session_launch" in json.dumps(value)
    assert not paths(shell.config).socket.exists()


def test_resource_paths_and_unknown_names_are_not_opened(shell):
    private = shell.root / "private-payload.txt"
    private.write_text("private-content-must-not-appear")
    for uri in (str(private), private.as_uri(), "workbench://missing"):
        result = shell("read", uri)
        failure(result, "not_found")
        assert "private-content-must-not-appear" not in result.stdout + result.stderr
    failure(shell("prompt", "missing", "--args", "{}"), "not_found")
    assert not paths(shell.config).socket.exists()


async def test_prompt_uses_registered_synthetic_sources_without_execution(knowledge):
    arguments = {"job_or_run_id": "synthetic_job"}
    expected = await runtime.one_shot(knowledge.config, "prompt", name="recover_or_cancel", arguments=arguments)
    assert "result" in expected
    result = success(knowledge("prompt", "recover_or_cancel", "--args", json.dumps(arguments), "--profile", "observe"))
    assert result == expected["result"]
    assert "Synthetic CLI guidance" in json.dumps(result)
    failure(knowledge("prompt", "recover_or_cancel", "--args", "{}"), "invalid_argument")
    failure(knowledge("prompt", "recover_or_cancel", "--args", "[]"), "invalid_argument")
    assert not paths(knowledge.config).socket.exists()


def test_missing_backend_remains_an_error_without_autostart(shell):
    failure(shell("call", "nightly_status", "--args", "{}"), "unavailable")
    failure(shell("prompt", "recover_or_cancel", "--args", '{"job_or_run_id":"synthetic"}'), "unavailable")
    assert not paths(shell.config).socket.exists()


def test_configuration_errors_are_sanitized(shell):
    shell.env["HOOKSHOT_WB_READ_ROOTS"] = '["relative/private-secret"]'
    error = failure(shell("list", "--json"), "invalid_argument")
    assert error == fail("invalid_argument", "configuration").envelope.model_dump()


def test_unexpected_backend_failure_has_no_traceback_or_path(shell):
    shell.config.state_root.mkdir()
    Registry(shell.config.state_root).path.write_bytes(b"not sqlite: secret backend /private/local/file")
    result = shell("call", "jobs_list", "--args", "{}")
    error = failure(result, "backend_failed")
    assert error["retryable"] is False
    assert "secret" not in result.stderr and "DatabaseError" not in result.stderr
    assert not paths(shell.config).socket.exists()


def test_broker_controls_never_autostart(shell):
    success(shell("broker", "status"))
    success(shell("broker", "stop"))
    assert not paths(shell.config).socket.exists()
    assert not paths(shell.config).owner.exists()


@pytest.mark.parametrize("tool,extra", [
    ("session_state", {}), ("session_options_get", {}), ("session_saves_list", {}),
    ("session_wait", {"predicate": {"kind": "frames", "frames": 1}, "idempotency_key": "missing-run"}),
])
def test_live_reads_use_broker_and_acknowledge_only_first_start(shell, tool, extra):
    arguments = json.dumps({"run_id": "run_" + "a" * 32, **extra})
    failure(shell("call", tool, "--args", arguments), "not_found", started=True)
    assert paths(shell.config).socket.exists()
    failure(shell("call", tool, "--args", arguments), "not_found")
    success(shell("broker", "status"))
    # An already-running broker never widens the selected CLI profile.
    failure(shell("call", "session_launch", "--args", "{}", "--profile", "observe"), "not_found")


def test_foreground_serve_has_no_banner_and_hides_inherited_options(shell):
    help_text = shell("serve", "--help")
    assert help_text.returncode == 0 and help_text.stderr == ""
    assert "--idle-seconds" in help_text.stdout and "--lifetime-seconds" in help_text.stdout
    assert "--config-fd" not in help_text.stdout and "--lock-fd" not in help_text.stdout


@pytest.mark.parametrize("arguments", [
    ("--idle-seconds", "0.1", "--lifetime-seconds", "10"),
    ("--idle-seconds", "60", "--lifetime-seconds", "0.1"),
])
def test_foreground_serve_obeys_idle_and_lifetime(shell, arguments):
    result = shell("serve", *arguments, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
    assert not paths(shell.config).socket.exists()


def test_launch_and_session_wait_return_jobs_without_waiting_for_lifetime(session):
    arguments = {"build_id": "synthetic", "profile_id": "ordinary", "wall_seconds": 120,
                 "idempotency_key": "cli-launch"}
    started = time.monotonic()
    launch = Job.model_validate(success(session("call", "session_launch", "--args", json.dumps(arguments),
        "--profile", "operate", "--timeout", "15"), started=True))
    assert time.monotonic() - started < 25
    assert launch.state not in TERMINAL and len(launch.run_ids) == 1
    run_id = launch.run_ids[0]
    observed(lambda: (session.root / "pids.json").exists())
    pids = json.loads((session.root / "pids.json").read_text())
    socket = session.root / "build/runs" / run_id / "control.sock"
    observed(socket.exists)
    assert all(identity(pid) is not None for pid in pids.values())

    # A fresh shell, even under observe, reaches the same broker-owned bridge.
    state = success(session("call", "session_state", "--args", json.dumps({"run_id": run_id})))
    assert state["game"]["map"] == "SyntheticZone"
    read = success(session("call", "job_read", "--args", json.dumps({"job_id": launch.job_id})))
    assert read["job"]["job_id"] == launch.job_id and read["job"]["state"] not in TERMINAL
    assert read["receipt"] is None
    logs = success(session("call", "run_logs", "--args", json.dumps({"run_id": run_id, "last_lines": 10})))
    assert "synthetic bridge starting" in json.dumps(logs) and logs["complete"] is False

    # This explicit observed-state wait has its own RPC deadline, not permission
    # to cancel or replay the underlying lifetime operation.
    failure(session("call", "job_wait", "--args", json.dumps({"job_id": launch.job_id, "timeout_seconds": 30}),
                    "--timeout", "0.2"), "timeout")
    assert all(identity(pid) is not None for pid in pids.values())
    waiting = Job.model_validate(success(session("call", "session_wait", "--args", json.dumps({
        "run_id": run_id, "predicate": {"kind": "map", "map": "NeverReached"},
        "timeout_seconds": 30, "idempotency_key": "cli-wait"}), "--timeout", "5")))
    assert waiting.state not in TERMINAL and waiting.run_ids == [run_id]
    assert paths(session.config).socket.exists()

    # The session adapter advertises a 30-second cleanup deadline; RPC budget
    # includes that deadline and receipt/lifespan bookkeeping.
    success(session("broker", "stop", "--timeout", "40", timeout=50))
    observed(lambda: all(identity(pid) is None for pid in pids.values()))
    observed(lambda: not paths(session.config).socket.exists())
