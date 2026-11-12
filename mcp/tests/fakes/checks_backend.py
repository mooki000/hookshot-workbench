"""Synthetic backend copied into temporary reviewed Git histories by checks tests."""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

CONTROL = Path("@CONTROL@")
control = json.loads(CONTROL.read_text())
name = Path(__file__).name
role = {"run_all.py": "source", "make_game.py": "build", "build.py": "build",
        "windows": "windows", "preflight": "pr", "gh": "ci", "copy": "copy"}.get(name, "gate")
if role == "gate":
    while control.get("queue"):
        time.sleep(0.02)
        control = json.loads(CONTROL.read_text())
    os.execv(sys.argv[1], sys.argv[1:])
if role == "copy":
    assert sys.argv[1:4] == ["-a", "--reflink=always", "--"]
    shutil.copytree(sys.argv[-2], sys.argv[-1])
    raise SystemExit(0)
if control.get("block") == role and "--dry-run" not in sys.argv:
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)"], start_new_session=True)
    Path(control["pids"]).write_text(json.dumps([os.getpid(), child.pid]))
    print("pass 0.1s tests/test_ready.py", flush=True)
    while True:
        time.sleep(0.02)
if role == "source":
    names = [s for s in sys.argv[1:] if s.startswith(("tests/", "nx2/"))]
    cases = ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_skip.py"]
    if control.get("many"):
        cases = ["tests/test_%03d.py" % i for i in range(120)]
    if names:
        cases = [case for case in cases if case in names]
    base = (Path.cwd() / "base_case.txt").read_text().strip() == "base"
    statuses = {case: "skip" if "skip" in case else "FAIL" if "alpha" in case or "beta" in case and not base else "pass" for case in cases}
    for case in cases:
        print(statuses[case] + " 0.1s " + case, flush=True)
    print("result time check")
    for case in cases:
        print(statuses[case] + " " + ("" if statuses[case] == "skip" else "0.1s ") + case)
    totals = [sum(s == state for s in statuses.values()) for state in ("pass", "FAIL", "skip")]
    print("%d passed, %d failed, %d skipped in 1 s" % tuple(totals), flush=True)
    raise SystemExit(bool(totals[1]))
if role == "build":
    print("version: fixture (source abc123)", flush=True)
    print("object keys: game-code headers aaaa, flags bbbb, hot flags cccc, lifted code dddd (1 files)", flush=True)
    if "--dry-run" in sys.argv:
        print("dry run: 2 files to compile besides the lift's, %d missing" % control.get("dry_missing", 0))
        raise SystemExit(bool(control.get("dry_missing")))
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    p.add_argument("--work", type=Path)
    args, _ = p.parse_known_args()
    out = args.out
    work = args.work or Path(os.environ["OOT_WORK"])
    (work / "sa/build").mkdir(parents=True, exist_ok=True)
    print("@@stage lift translating", flush=True)
    print("@@stage compile compiling", flush=True)
    print("@@plan 2 2", flush=True)
    print("@@file unit.c 0.1 new", flush=True)
    print("compiled in 1s, 0 failed", flush=True)
    for item in ("hookshot", "hookshot_vk.so"):
        (work / "sa/build" / item).write_text("neutral binary")
        print("linked %s: 1 MB (0s)" % (work / "sa/build" / item), flush=True)
    print("@@stage finish packaging", flush=True)
    if out:
        out.mkdir()
        for item in ("hookshot", "hookshot_vk.so"):
            (out / item).write_text("neutral binary")
        if not control.get("no_receipt"):
            print("@@done " + str(out), flush=True)
if role == "windows":
    p = argparse.ArgumentParser()
    p.add_argument("worktree", type=Path)
    p.add_argument("base")
    p.add_argument("--output", type=Path)
    args, _ = p.parse_known_args()
    args.output.mkdir()
    commit = subprocess.check_output(["git", "-C", str(args.worktree), "rev-parse", "HEAD"], text=True).strip()
    report = {"head": commit, "base": args.base, "tests": [{"records": [{"status": "PASS", "command": ["clang", "--target=x86_64-w64-windows-gnu", "/private/work/unit.c"]}]}], "unjudged": ["unreached fixture"]}
    (args.output / "report.json").write_text(json.dumps(report))
    print("PASS tests/test_alpha.py -> generated.c", flush=True)
    raise SystemExit(2)
if role == "pr":
    print("INFO branch candidate: 1 commits on abcdef; origin/main is abcdef")
    print("OK preserved imported authorship")
    print("WARN fixture /private/work/thing token=hidden")
    print("PASS")
if role == "ci":
    args = sys.argv[1:]
    endpoint = next((arg for arg in args if arg.startswith("repos/")), "")
    if args[0] == "pr":
        print(json.dumps({"number": 7, "headRefOid": "a" * 40, "reviewDecision": "REVIEW_REQUIRED", "statusCheckRollup": [
            {"name": "windows", "conclusion": "FAILURE", "detailsUrl": "https://github.com/Example/Project/actions/runs/11/job/101"},
            {"name": "linux", "conclusion": "SUCCESS", "detailsUrl": "https://github.com/Example/Project/actions/runs/11/job/102"}]}))
    elif "branch=main" in endpoint:
        print(json.dumps({"workflow_runs": [] if control.get("no_main") else [{"id": 10, "workflow_id": 1, "head_sha": "b" * 40, "status": "completed"}]}))
    elif endpoint.endswith("/logs"):
        if control.get("log_error"):
            print("secret /private/credential/path")
            raise SystemExit(1)
        print("2026-01-01T00:00:00Z FAIL 0.2s tests/test_alpha.py")
    elif "/jobs?" in endpoint:
        print(json.dumps({"total_count": 2, "jobs": [
            {"id": 101, "name": "windows", "conclusion": "failure", "html_url": "https://github.com/Example/Project/actions/runs/11/job/101"},
            {"id": 102, "name": "linux", "conclusion": "success"}]}))
    elif "/actions/jobs/" in endpoint:
        print(json.dumps({"run_id": 11}))
    elif "/actions/runs/" in endpoint:
        print(json.dumps({"id": 11, "workflow_id": 1, "head_sha": "a" * 40, "status": "completed"}))
    else:
        raise SystemExit(3)
