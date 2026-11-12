"""Synthetic official-reader CLI. Tests copy it into a temporary registered checkout."""
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
state = json.loads((root / "backend.json").read_text())
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if "--help" in sys.argv:
    print("--room --json --limit --cursor " + ("--read-only" if state.get("safe", True) else ""))
    raise SystemExit(0)
if state.get("unavailable"):
    raise SystemExit(4)
if state.get("overflow"):
    sys.stdout.write("x" * (5 * 1024 * 1024))
    raise SystemExit(0)
if state.get("error"):
    print("private backend failure /private/local/credential", file=sys.stderr)
    raise SystemExit(2)
if state.get("block"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (root / "child.pid").write_text(str(child.pid))
    (root / "worker.pid").write_text(str(__import__("os").getpid()))
    time.sleep(120)
if Path(__file__).name == "gh":
    assert sys.argv[1:7] == ["api", "--method", "GET", "--hostname", "github.com", sys.argv[6]]
    endpoint = sys.argv[-1]
    if endpoint not in state["pages"]:
        print("private unknown endpoint", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(state["pages"][endpoint]))
else:
    assert "--read-only" in sys.argv and "--json" in sys.argv and "--room" in sys.argv
    if Path(__file__).name == "test_requests.py":
        print(json.dumps(state["cards"][0] if sys.argv[1] == "show" else state["cards"]))
    else:
        cursor = sys.argv[sys.argv.index("--cursor") + 1] if "--cursor" in sys.argv else None
        offset = int(cursor or 0)
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
        messages = state["messages"][offset:offset + limit]
        more = offset + limit < len(state["messages"])
        print(json.dumps({"messages": messages, "next_cursor": str(offset + limit) if more else None, "complete": not more}))
