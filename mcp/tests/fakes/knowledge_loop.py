"""Synthetic installed loop exposing the stock scan and pure-view interfaces."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = "example/project"


@contextmanager
def locked(directory):
    with (directory / "loop.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Backend:
    def __init__(self, checkout):
        self.checkout = checkout


def validate_state(state):
    assert state["version"] == 1


def validate_config(config):
    assert config["version"] == 1


def next_action(state, config, now):
    if config.get("error"):
        raise RuntimeError("private /private/local/session")
    return {"action": "diff-review", "number": 7, "why": "Review the exact synthetic executable head."}


def exclusions(state, config):
    return ["Another synthetic request does not fit."]


def scan(state, config, backend, now):
    root = Path(config["fixture_root"])
    with (root / "scans").open("a") as stream:
        stream.write("scan\n")
    if config.get("block"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (root / "child.pid").write_text(str(child.pid))
        (root / "worker.pid").write_text(str(os.getpid()))
        time.sleep(120)
    state["snapshot"]["at"] = now
    state["snapshot"]["prs"]["7"]["head"] = "b" * 40
    return state


def atomic_json(path, value):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)
    if json.loads((path.parent / "config.json").read_text()).get("after_write_error"):
        raise RuntimeError("private failure after rename /private/local/file")
