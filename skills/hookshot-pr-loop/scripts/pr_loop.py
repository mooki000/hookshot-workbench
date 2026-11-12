#!/usr/bin/env python3
"""Local, read-only-on-the-network queue for testing other people's Hookshot PRs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = "NotRexed/Hookshot"
STATE_DIR = Path("/home/mooki/Games/.hookshot-harness/pr-loop")
CHECKOUT = "/home/mooki/Code/hookshot-wt/main-tools"
CLONE = Path("/home/mooki/Code/Hookshot")
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
PRIORITY_GROUP = ["nullifyshot", "hckrman101", "sadtransgirl", "klimovich008", "madebyjordan"]
WAITS = ("author-reply", "new-commits", "maintainer", "our-test", "our-reply")
VERDICTS = ("pass", "fail", "mixed")
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUEST_ID = re.compile(r"^T-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}$")
FIELDS = "number,author,title,body,isDraft,headRefOid,createdAt,updatedAt,state,url,labels"


class LoopError(Exception):
    pass


def clock():
    """Use the host's date command for recorded timestamps, not a model's clock."""
    return subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], check=True,
                          capture_output=True, text=True).stdout.strip()


def epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        raise LoopError("a timestamp must be an ISO date with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.timestamp()
    except ValueError as error:
        raise LoopError("invalid timestamp: " + value) from error


def stamp(value):
    return datetime.fromtimestamp(epoch(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(text):
    # Tracker text is data. Do not let terminal escape sequences or table bars act as formatting.
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text)).split()).replace("|", "\\|")


def require(condition, message):
    if not condition:
        raise LoopError(message)


def read_json(path):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (ValueError, UnicodeError) as error:
        raise LoopError(f"refusing corrupt {path.name}; original file left untouched: {error}") from error


def atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never unlink the lock: another process could otherwise lock a different inode.
    fd = os.open(directory / "loop.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LoopError("another pr_loop command holds the lock; nothing changed") from error
        yield
    finally:
        os.close(fd)


def defaults(authors, provenance, now):
    priority = []
    for author in [*PRIORITY_GROUP, "NotRexed", *sorted(authors, key=str.lower)]:
        if author.lower() not in {a.lower() for a in priority} and author.lower() != "mooki000":
            priority.append(author)
    return {
        "version": 1, "repo": REPO, "user": "mooki000", "checkout": CHECKOUT,
        "priority_authors": priority + ["everyone else"], "skip_authors": ["mooki000"],
        "round_robin_authors": list(PRIORITY_GROUP),
        "comment": 'User\'s list: "null", "hckrman101", "sadtransgirl", "klimovich008", "madebyjordan". '
                   '"null" is nullifyshot on GitHub; "Jordan" is madebyjordan. Rotate across these authors; listed order breaks ties.',
        "notes": [{"at": now, "action": "initial priorities", "basis": "on the user's word",
                   "authors": list(PRIORITY_GROUP)}],
        "priority_source": {"at": now, "method": provenance}, "request_user": None,
        "hardware": {
            "os": "Arch Linux", "cpu": "Intel Core i9-13900K", "display": "1920x1080, 60 Hz, no HDR",
            "gpus": [
                {"vendor": "NVIDIA", "model": "RTX 4090", "vram_gib": 24,
                 "driver": "NVIDIA 615.78.08", "mesh_shaders": True},
                {"vendor": "Intel", "model": "UHD 770", "vram_gib": None,
                 "driver": "Mesa 26.2.5 (Arch package 1:26.2.5-1)", "mesh_shaders": False,
                 "memory": "shared system RAM, not a fixed-size discrete VRAM card"}],
            "windowless_only": True,
        },
        "max_active": 4, "stale_days": 3,
    }


def validate_config(config):
    require(isinstance(config, dict) and config.get("version") == 1, "unsupported config version")
    require(config.get("repo") == REPO, "this loop is for " + REPO)
    for key in ("user", "checkout"):
        require(isinstance(config.get(key), str) and config[key].strip(), "config needs " + key)
    for key in ("priority_authors", "skip_authors"):
        require(isinstance(config.get(key), list) and all(isinstance(a, str) and a.strip() for a in config[key]),
                "config needs a list of logins: " + key)
        require(len({a.lower() for a in config[key]}) == len(config[key]), "duplicate " + key)
    require(config["priority_authors"] and config["priority_authors"][-1] == "everyone else",
            'priority_authors must end with "everyone else"')
    group = config.get("round_robin_authors", [])
    require(isinstance(group, list) and all(isinstance(a, str) and a.lower() in
            {p.lower() for p in config["priority_authors"][:-1]} for a in group), "invalid round_robin_authors")
    require(len({a.lower() for a in group}) == len(group), "duplicate round_robin_authors")
    require(config["user"].lower() in {a.lower() for a in config["skip_authors"]}, "skip_authors must include the user")
    require(type(config.get("max_active")) is int and config["max_active"] > 0, "max_active must be a positive integer")
    require(type(config.get("stale_days")) in (int, float) and 0 < config["stale_days"] <= 365,
            "stale_days must be positive and at most 365")
    hardware = config.get("hardware")
    require(isinstance(hardware, dict) and isinstance(hardware.get("os"), str), "hardware needs an OS")
    require(isinstance(hardware.get("gpus"), list) and hardware["gpus"], "hardware needs GPUs")
    for gpu in hardware["gpus"]:
        require(isinstance(gpu, dict) and all(isinstance(gpu.get(k), str) for k in ("vendor", "model", "driver")),
                "each GPU needs vendor, model and driver")
        require(gpu.get("vram_gib") is None or type(gpu["vram_gib"]) in (int, float), "invalid GPU VRAM")


def new_state():
    return {"version": 1, "active": {}, "done": {}, "parked": {}, "snapshot": None}


def validate_state(state):
    require(isinstance(state, dict) and state.get("version") == 1, "refusing corrupt/unsupported state version")
    seen = set()
    for bucket in ("active", "done", "parked"):
        require(isinstance(state.get(bucket), dict), "refusing corrupt state: missing " + bucket)
        for key, record in state[bucket].items():
            require(isinstance(record, dict) and str(record.get("number")) == key and key.isdecimal(),
                    "refusing corrupt state record " + key)
            require(key not in seen, "refusing duplicate state record " + key)
            seen.add(key)
            for field in ("author", "title", "why", "joined_at", "wait_for", "wait_since", "history",
                          "last_tested_head", "last_tested_at", "verdict", "comment_url", "claim", "request_id"):
                require(field in record, f"refusing corrupt record #{key}: missing {field}")
            require(record["wait_for"] in WAITS and isinstance(record["history"], list), "invalid wait/history")
            epoch(record["joined_at"])
            epoch(record["wait_since"])
            if record["last_tested_head"] is not None:
                require(SHA.fullmatch(record["last_tested_head"]) is not None and
                        record["verdict"] in VERDICTS and record["comment_url"], "invalid tested record")
                epoch(record["last_tested_at"])
            if record.get("carry") is not None:
                proof = record["carry"]
                require(isinstance(proof, dict) and all(isinstance(proof.get(k), str) and SHA.fullmatch(proof[k])
                        for k in ("from_head", "to_head", "from_base", "to_base", "main_head")), "invalid carry heads/bases")
                require(proof["from_head"] == record["last_tested_head"] and proof["to_head"] != proof["from_head"],
                        "carry must start at the actually tested head")
                require(type(proof.get("patch_commits")) is int and proof["patch_commits"] > 0
                        and type(proof.get("main_commits")) is int and proof["main_commits"] >= 0
                        and isinstance(proof.get("range_diff"), str), "invalid carry proof")
                epoch(proof.get("at"))
            if record.get("gap_override") is not None:
                gap = record["gap_override"]
                require(isinstance(gap, dict) and isinstance(gap.get("quote"), str) and len(gap["quote"]) >= 20
                        and isinstance(gap.get("reason"), str) and gap["reason"].strip()
                        and isinstance(gap.get("source_url"), str) and isinstance(gap.get("source_kind"), str)
                        and isinstance(gap.get("head"), str) and SHA.fullmatch(gap["head"]), "invalid gap override")
                epoch(gap.get("at"))
            if record["claim"] is not None:
                require(isinstance(record["claim"], dict) and
                        record["claim"].get("status") in ("active", "expired", "released", "finished"), "invalid claim")
                epoch(record["claim"]["expires_at"])
    snapshot = state.get("snapshot")
    require(snapshot is None or isinstance(snapshot, dict) and isinstance(snapshot.get("prs"), dict)
            and isinstance(snapshot.get("requests"), list) and isinstance(snapshot.get("warnings"), list),
            "refusing corrupt snapshot")
    if snapshot:
        epoch(snapshot["at"])


# The project's list command may join a room and save ~/.config on first use. Run the
# same trusted modules with those two routes refused, so even list remains read-only.
REQUEST_READER = r'''
import contextlib, importlib.util, io, json, pathlib, sys
root = pathlib.Path.cwd()
sys.path.insert(0, str(root / "scripts"))
import team_chat
original = team_chat.request
def read_only(method, *args, **kwargs):
    if method != "GET":
        raise team_chat.ChatError("read-only loop refuses room join or write; main session must arrange access")
    return original(method, *args, **kwargs)
def no_save(*args, **kwargs):
    raise team_chat.ChatError("read-only loop refuses a config write")
team_chat.request, team_chat.save = read_only, no_save
import test_requests
out = io.StringIO()
with contextlib.redirect_stdout(out):
    status = test_requests.main(["list", "--json"])
if status:
    sys.exit(status)
print(json.dumps({"requests": json.loads(out.getvalue()), "user_id": team_chat.load().get("user_id")}))
'''


class Backend:
    def __init__(self, checkout=CHECKOUT, runner=subprocess.run):
        self.checkout = checkout
        self.runner = runner

    def run(self, command, timeout=120, env=None):
        try:
            result = self.runner(command, cwd=self.checkout, capture_output=True, text=True,
                                 timeout=timeout, env=env)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LoopError("read command failed: " + str(error)) from error
        if result.returncode:
            raise LoopError(clean(result.stderr or result.stdout or "read command failed"))
        return result

    def json(self, command):
        result = self.run(command)
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise LoopError("read command did not return JSON") from error

    def api(self, endpoint, pages=False):
        command = ["gh", "api"]
        if pages:
            command += ["--paginate", "--slurp"]
        value = self.json(command + ["repos/" + REPO + "/" + endpoint])
        return [item for page in value for item in page] if pages else value

    def authors(self):
        try:
            rows = self.api("collaborators?per_page=100", pages=True)
            return [row["login"] for row in rows], "GitHub collaborators endpoint"
        except LoopError as error:
            # A contributor cannot list collaborators. Association is positive evidence,
            # but this bounded fallback is not a claim to know the whole team.
            rows = self.api("pulls?state=all&per_page=100")
            authors = {row["user"]["login"] for row in rows if row.get("author_association") in TRUSTED}
            return sorted(authors), "recent 100 PR associations; collaborator listing unavailable: " + str(error)

    def validate_author(self, handle):
        require(bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", handle))
                and "--" not in handle, "invalid GitHub handle")
        user = self.json(["gh", "api", "users/" + handle])
        require(user.get("type") == "User" and isinstance(user.get("login"), str), "handle must name a GitHub user")
        login = user["login"]
        pulls = self.json(["gh", "pr", "list", "--repo", REPO, "--state", "all", "--author", login,
                           "--limit", "1", "--json", "number,author"])
        require(isinstance(pulls, list) and any(p.get("author", {}).get("login", "").lower() == login.lower()
                                             for p in pulls), "handle has not authored a PR on " + REPO)
        return login

    def requests(self):
        warnings = []
        token_names = ("GH_TOKEN", "GITHUB_TOKEN")
        env = {name: value for name, value in os.environ.items() if name not in token_names}
        exported = [name for name in token_names if name in os.environ]
        for name in exported:
            env[name] = os.environ[name]
        if exported:
            warnings.append("Request reader: passing through session-exported " + ", ".join(exported)
                            + " unchanged; no credential lookup (values not printed)")
        try:
            result = self.run([sys.executable, "-I", "-B", "-c", REQUEST_READER], timeout=300, env=env)
            data = json.loads(result.stdout)
            require(isinstance(data.get("requests"), list), "request list has the wrong shape")
            if result.stderr.strip():
                warnings.append("test_requests.py: " + clean(result.stderr))
            mark_request_annotations(data["requests"], result.stderr)
            return data["requests"], data.get("user_id"), warnings, True
        except (LoopError, ValueError) as error:
            warnings.append("Test requests unavailable; continuing without them: " + str(error))
            return [], None, warnings, False

    def open_prs(self):
        rows = self.json(["gh", "pr", "list", "--repo", REPO, "--state", "open", "--limit", "1000", "--json", FIELDS])
        require(isinstance(rows, list) and len(rows) < 1000, "PR list may be truncated; refusing incomplete scan")
        # REST supplies author_association, which gh pr list's JSON does not expose.
        rest = {row["number"]: row for row in self.api("pulls?state=open&per_page=100", pages=True)}
        result = {}
        for row in rows:
            number = row["number"]
            info = rest.get(number) or self.api(f"pulls/{number}")
            result[str(number)] = normalize_pr(info)
        # Keep PRs opened between the two reads, too.
        for number, info in rest.items():
            result.setdefault(str(number), normalize_pr(info))
        return result

    def detail(self, number):
        return normalize_pr(self.api(f"pulls/{number}"))

    def enrich(self, pr):
        number = pr["number"]
        events = []
        for endpoint, kind in ((f"issues/{number}/comments?per_page=100", "comment"),
                               (f"pulls/{number}/reviews?per_page=100", "review"),
                               (f"pulls/{number}/comments?per_page=100", "inline")):
            for row in self.api(endpoint, pages=True):
                created = row.get("submitted_at") or row.get("created_at")
                if not created:  # Unsubmitted private reviews are not replies.
                    continue
                events.append({"id": f"{kind}:{row['id']}", "kind": kind, "author": (row.get("user") or {}).get("login", ""),
                               "association": row.get("author_association", ""), "at": row.get("updated_at") or created,
                               "created_at": created, "body": row.get("body") or "", "url": row.get("html_url", ""),
                               "state": row.get("state"), "commit": row.get("commit_id")})
        pr["events"] = sorted(events, key=lambda e: (epoch(e["at"]), e["id"]))
        if pr["state"] == "OPEN":
            files = self.api(f"pulls/{number}/files?per_page=100", pages=True)
            require(len(files) == pr["changed_files"], f"#{number}: changed-file list incomplete or changed during scan")
            pr["files"] = [row["filename"] for row in files]
        else:
            pr["files"] = []
        return pr


def normalize_pr(row):
    return {"number": row["number"], "author": row["user"]["login"], "title": row["title"],
            "body": row.get("body") or "", "draft": row.get("draft", False),
            "association": row.get("author_association", ""), "head": row["head"]["sha"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "url": row["html_url"],
            "state": "MERGED" if row.get("merged_at") else row["state"].upper(),
            "ended_at": row.get("merged_at") or row.get("closed_at"),
            "labels": [label["name"] for label in row.get("labels", [])],
            "changed_files": row.get("changed_files"), "events": [], "files": []}


def priority(author, config):
    group = {a.lower() for a in config.get("round_robin_authors", [])}
    if author.lower() in group:
        return 1
    authors = [a.lower() for a in config["priority_authors"] if a.lower() not in group]
    return (authors.index(author.lower()) if author.lower() in authors else len(authors) - 1) + 2


def hardware_fit(needs, hardware):
    """Conservative routing, not proof that a free-text request can be fulfilled."""
    text = needs.lower().strip()
    if not text or text in ("any", "any pc", "any hardware", "all hardware"):
        return "fit", "no hardware restriction"
    os_names = re.findall(r"\b(windows|linux|arch|steamos|macos|mac os|steam deck)\b", text)
    if os_names and not any(name in hardware["os"].lower() for name in os_names):
        return "no", "requested OS or Steam Deck is not this PC"
    if re.search(r"\b(hdr|controller|gamepad)\b", text):
        return "unknown", "needs display/input capability not established by this PC profile"
    if hardware.get("windowless_only") and re.search(r"\b(windowed|swapchain|compositor)\b", text):
        return "no", "requires a window; this PC is currently windowless-only"
    models = re.findall(r"\b(rtx|gtx|rx|arc|uhd|iris)\s*([ab]?\d{2,4})?", text)
    vendors = set(re.findall(r"\b(nvidia|amd|intel)\b", text))
    models = [(family, number) for family, number in models]
    no_mesh = bool(re.search(r"(?:no|without|lack\w*) mesh shaders?", text))
    memory = re.search(r"(?:(at least|minimum|at most|up to|under|less than)\s*)?(\d+)\s*(?:gb|gib)\b", text)
    known = bool(os_names or models or vendors or no_mesh or memory or "mesa" in text or "integrated" in text)
    if not known:
        return "unknown", "free-text hardware need requires the main session's reading"
    for gpu in hardware["gpus"]:
        model, vendor = gpu["model"].lower(), gpu["vendor"].lower()
        if vendors and vendor not in vendors:
            # A named family may be an alternative to an explicit vendor.
            family_vendor = {"rtx": "nvidia", "gtx": "nvidia", "rx": "amd", "arc": "intel", "uhd": "intel", "iris": "intel"}
            if not any(family_vendor[f] == vendor for f, _ in models):
                continue
        if models:
            match = False
            for family, number in models:
                actual = re.search(r"\b" + family + r"\s*([ab]?\d+)", model)
                if not actual:
                    continue
                wanted, have = number.lstrip("ab"), actual.group(1).lstrip("ab")
                if not number or have == wanted or (len(wanted) == 2 and have.startswith(wanted)):
                    match = True
                elif wanted.endswith("00") and have[:1] == wanted[:1] and len(have) == len(wanted):
                    match = True  # RX 6000 means that series, not a discrete RX 6000 model.
                elif re.search(re.escape(number) + r"\s*(?:or newer|and newer|\+)", text) and int(have) >= int(wanted):
                    match = True
                elif re.search(re.escape(number) + r"\s*[-–]\s*(\d+)", text):
                    end = re.search(re.escape(number) + r"\s*[-–]\s*(\d+)", text).group(1)
                    value = int(have[:len(wanted)]) if len(wanted) < len(have) else int(have)
                    match |= int(wanted) <= value <= int(end)
            if not match:
                continue
        if "mesa" in text and "mesa" not in gpu["driver"].lower():
            continue
        if "integrated" in text and gpu.get("vram_gib") is not None:
            continue
        if no_mesh and gpu.get("mesh_shaders") is not False:
            continue
        if memory:
            size = gpu.get("vram_gib")
            if size is None:
                continue  # Shared RAM is not an 8 GB discrete card.
            relation, wanted = memory.group(1), int(memory.group(2))
            if relation in ("at least", "minimum") and size < wanted:
                continue
            if relation in ("at most", "up to") and size > wanted:
                continue
            if relation in ("under", "less than") and size >= wanted:
                continue
            if not relation and size != wanted:
                continue
        return "fit", gpu["model"] + " on " + hardware["os"]
    return "no", "no installed GPU meets the named family, driver, memory and feature requirements"


UNTESTED = re.compile(r"\b(?:not (?:yet )?(?:tested|tried|run|measured)|untested)\b", re.I)
GPU_WORDS = re.compile(r"\b(?:nvidia|amd|intel|rtx|gtx|rx|arc|uhd|iris)\b", re.I)


def tested_hardware_lines(body):
    """Only positive testing prose can rule out an 'other models' hardware gap."""
    active, lines = False, []
    for line in body.splitlines():
        negative = UNTESTED.search(line)
        positive = re.search(r"\b(?:tested|verified|measured)\b", line[:negative.start()] if negative else line, re.I)
        if line.lstrip().startswith("#"):
            active = bool(positive)
        elif positive:
            active = True
        if active:
            lines.append(line[:negative.start()] if negative else line)
        if negative:
            active = False
    return lines


def model_was_tested(gpu, lines):
    model = re.search(r"\b(?:rtx|gtx|rx|arc|uhd|iris)\s*(?:[ab]?\d{2,4}|xe)\b", gpu["model"], re.I)
    parts = re.findall(r"[a-z]+|\d+", (model.group() if model else gpu["model"]).lower())
    exact = re.compile(r"(?<![a-z0-9])" + r"[\s_-]*".join(re.escape(p) for p in parts) + r"(?![a-z0-9])", re.I)
    for line in lines:
        if exact.search(line):
            return True
        # A tester may write just 'a 4090'; do not mistake an explicitly different vendor/family or CPU for it.
        if (gpu["vendor"].lower() == "nvidia" and parts and parts[-1].isdigit() and len(parts[-1]) == 4
                and not GPU_WORDS.search(line) and not re.search(r"\b(?:cpu|core|ryzen)\b", line, re.I)
                and re.search(r"\b" + re.escape(parts[-1]) + r"\b", line)):
            return True
    return False


def gap_clauses(line):
    # These are separate gaps, not request-card constraints: 'other Intel GPUs, Windows'.
    # An explicit trailing 'on Windows' still constrains the preceding GPU clause.
    clauses = []
    for part in re.split(r"\s*[,;]\s*", line):
        if clauses and re.match(r"^(?:on|under|running|with)\s+", part, re.I):
            clauses[-1] += " " + part
        else:
            clauses.append(part)
    return clauses


def not_tested_gap(body, hardware):
    section = False
    tested = tested_hardware_lines(body)
    for line in body.splitlines():
        marker = UNTESTED.search(line)
        if line.lstrip().startswith("#"):
            section = bool(marker)
        if marker:
            section = True
            line = line[marker.end():]
        if not section or not line.strip(" :*-#"):
            continue
        for clause in gap_clauses(line):
            if GPU_WORDS.search(clause):
                for gpu in hardware["gpus"]:
                    if (not model_was_tested(gpu, tested)
                            and hardware_fit(clause, {**hardware, "gpus": [gpu]})[0] == "fit"):
                        return clean(line)
            elif re.search(r"\b(?:linux|arch|mesa)\b|mesh shader", clause, re.I):
                if hardware_fit(clause, hardware)[0] == "fit":
                    return clean(line)
    return None


def gap_sources(pr):
    yield "description", pr["url"], " ".join(pr["body"].split())
    for event in pr["events"]:
        yield event["kind"], event["url"], " ".join(event["body"].split())


def parse_gap_override(pr, text, now):
    note = " ".join(text.split())
    require(len(note) >= 20, "--gap needs a quoted excerpt of at least 20 characters and why this PC fills it")
    sources = list(gap_sources(pr))
    quoted = list(re.finditer(r'"([^"]{20,})"|“([^”]{20,})”|`([^`]{20,})`', note))
    found = None
    if quoted:
        for match in quoted:
            quote = next(group for group in match.groups() if group is not None).strip()
            if len(quote) < 20:
                continue
            source = next((s for s in sources if quote in s[2]), None)
            if source:
                found = (quote, note[:match.start()] + " " + note[match.end():], source)
                break
    else:
        # Plain 'source sentence — reason' is also a quotation; retain its actual matched text.
        longest = 0
        for source in sources:
            match = SequenceMatcher(None, note, source[2], autojunk=False).find_longest_match()
            quote = note[match.a:match.a + match.size].strip()
            if len(quote) >= 20 and len(quote) > longest:
                found = (quote, note[:match.a] + " " + note[match.a + match.size:], source)
                longest = len(quote)
    require(found is not None, "--gap needs an exact 20-character-or-longer quote from the snapshot's description or thread")
    quote, reason, source = found
    reason = reason.strip(" \t\"'“”‘’`:;—–-")
    require(bool(re.search(r"\w", reason)), "--gap also needs a reason why this PC fills the quoted gap")
    return {"quote": quote, "reason": reason, "text": text.strip(), "source_kind": source[0],
            "source_url": source[1], "head": pr["head"], "at": now}


def parked_description(pr):
    first = "\n".join(pr["body"].splitlines()[:12])
    for line in first.splitlines():
        if re.search(r"\b(?:parked|not for testing|not ready for testing|hold off|do not test|don't test|stop testing)\b", line, re.I):
            if not re.search(r"\b(?:no longer parked|unparked|not parked)\b", line, re.I):
                return clean(line)
    return None


def risky_files(pr):
    return [name for name in pr["files"] if name.startswith(("scripts/", "tools/", ".github/", "tests/"))
            or name.endswith((".py", ".sh", ".ps1", ".bat", ".cmd", ".cmake"))
            or Path(name).name.lower() in ("makefile", "cmakelists.txt", "requirements.txt", "pyproject.toml")]


def unexplained_files(pr):
    body = pr["body"].lower()
    return [name for name in risky_files(pr) if name.lower() not in body and Path(name).name.lower() not in body
            and Path(name).stem.lower() not in body]


def own_test(pr, user):
    for event in reversed(pr["events"]):
        if event["author"].lower() != user.lower():
            continue
        for line in event["body"].splitlines():
            if re.search(r"\b(?:not tested|not built|will test|will build|haven't tested|have not tested)\b", line, re.I):
                continue
            match = re.search(r"\b(?:built|tested)(?:\s+(?:head|commit|on|at))?\s*[:=]?\s*`?([0-9a-f]{7,40})\b", line, re.I)
            if match and pr["head"].startswith(match.group(1).lower()):
                return event
    return None


def comment_url(url, number):
    return bool(re.fullmatch(r"https://github\.com/NotRexed/Hookshot/pull/" + str(number)
                            + r"#(?:issuecomment-\d+|pullrequestreview-\d+|discussion_r\d+)", url))


def effective_tested_head(record):
    proof = record.get("carry")
    return proof["to_head"] if proof else record["last_tested_head"]


def carry_label(record):
    proof = record.get("carry")
    if not proof:
        return ""
    return (f"tested at {proof['from_head']}, carried to {proof['to_head']} "
            f"(identical patches, {proof['main_commits']} main commits apart)")


def record_reason(record):
    carried = carry_label(record)
    return record["why"]["text"] + ("; " + carried if carried else "")


def candidate(pr, state, config, requests=None, review=None, gap_override=None):
    reasons = []
    key = str(pr["number"])
    record = state["active"].get(key) or state["parked"].get(key) or state["done"].get(key)
    gap_override = gap_override or (record or {}).get("gap_override")
    named = pr["author"].lower() in {a.lower() for a in config["priority_authors"][:-1]}
    if pr["state"] != "OPEN":
        reasons.append("PR is " + pr["state"].lower())
    if pr["author"].lower() in {a.lower() for a in config["skip_authors"]}:
        reasons.append("skip_authors: the loop tests other people's work, not this user's PRs")
    if key in state["parked"]:
        reasons.append("locally parked: " + record["end_reason"])
    if key in state["done"]:
        reasons.append("already in done; an explicit take --reopen is required")
    if pr.get("scan_error"):
        reasons.append("incomplete read: " + pr["scan_error"])
    parked = parked_description(pr)
    if parked:
        reasons.append("author says to hold off: " + parked)
    tested = own_test(pr, config["user"])
    if record and effective_tested_head(record) == pr["head"] and record.get("carry"):
        reasons.append("tested result carried: " + carry_label(record))
    elif record and record["last_tested_head"] == pr["head"] or tested:
        reasons.append("user already tested this head" + (": " + tested["url"] if tested else ""))
    requests = requests if requests is not None else (state["snapshot"] or {}).get("requests", [])
    linked = [r for r in requests if r.get("pr") == pr["number"] and r.get("status") == "open"]
    request_feed_live = (state["snapshot"] or {}).get("requests_available", True)
    invited = bool(re.search(r"\b(?:please test|test(?:ing|ers)? (?:wanted|welcome|requested)|"
                             r"needs? (?:a |more )?test(?:ing)?|ready for testing|test this|help test|"
                             r"waiting for (?:hands-on )?testing)\b",
                             pr["body"], re.I))
    card_invitation = bool(linked) if named else any(r.get("approved") is True and request_feed_live for r in linked)
    if pr["draft"] and not card_invitation and not invited:
        reasons.append("draft has no testing invitation or approved request card; do not assume it is ready")
    choices, request_notes, deferred = [], [], []
    for request in linked:
        fit, detail = hardware_fit(request.get("needs", ""), config["hardware"])
        approved = request.get("approved") is True and request_feed_live
        request_notes.append({"id": request["id"], "fit": fit, "detail": detail, "approved": approved})
        if not approved:
            deferred.append(request["id"])
        elif fit == "fit":
            choices.append(request)
    if linked and not choices and not gap_override and all(note["approved"] for note in request_notes):
        reasons.append("no open request fits this PC: " + "; ".join(n["id"] + " " + n["detail"] for n in request_notes))
    review = review or (record or {}).get("code_review")
    unexplained = unexplained_files(pr)
    if unexplained and not (review and review.get("head") == pr["head"] and review.get("note")):
        reasons.append("main session must inspect unexplained executable/tool changes before any build: " + ", ".join(unexplained))
    choices.sort(key=lambda r: (bool(r.get("results")), bool(r.get("claims")), r.get("requested", 0), r["id"]))
    request = choices[0] if choices else None
    gap = not_tested_gap(pr["body"], config["hardware"])
    asked = next((e for e in reversed(pr["events"]) if
                  (e["author"] == pr["author"] or e["association"] in TRUSTED)
                  and re.search(r"\b(?:please test|could .{0,30}test|needs? testing|test(?:ing|ers)? (?:wanted|welcome)|"
                                r"can .{0,30}(?:test|try|check)|@" + re.escape(config["user"]) +
                                r".{0,80}(?:test|try|check|retest))\b", e["body"], re.I)), None)
    if not named and not request and not gap and not gap_override and not asked and not invited:
        reasons.append("lowest tier needs a testing request in the description/thread or a hardware gap this PC fills")
        if deferred:
            reasons.append("unapproved card is not an invitation by itself: " + ", ".join(deferred))
        if linked and not request_feed_live:
            reasons.append("request feed unavailable; saved card approval/claim is not current")
    admission = "card approved" if request else "priority author on the user's word" if named else "asked in the thread/description"
    if gap_override:
        admission = (admission + "; " if request or named else "") + "main-session hardware-gap judgement"
    card_notes = [f"card {tid} exists but is not approved: no claim" if request_feed_live
                  else f"card {tid} approval is not current: no claim" for tid in deferred]
    if card_notes:
        admission += "; " + "; ".join(card_notes)
    if gap_override:
        rank, why = 2, {"kind": "gap-override",
                       "text": f'main-session gap: "{gap_override["quote"]}" — {gap_override["reason"]} ({gap_override["source_url"]})'}
    elif request:
        rank = 0 if not request.get("results") and not request.get("claims") else 1 if not request.get("results") else 3
        why = {"kind": "test-request", "request_id": request["id"],
               "text": request["id"] + " fits this PC; " + ("nobody has tested it" if not request.get("results") else "more hardware evidence requested")}
    elif gap:
        rank, why = 2, {"kind": "not-tested", "text": "description's untested hardware gap: " + gap}
    elif asked or invited:
        rank, why = 4, {"kind": "asked", "text": asked["author"] + " asked for our check: " + asked["url"]
                       if asked else "author asks for testing in the description"}
    else:
        rank, why = 5, {"kind": "priority", "text": "picked by author priority; oldest first within this tier and need"}
    if card_notes:
        why["text"] += "; " + "; ".join(card_notes)
    return {"number": pr["number"], "author": pr["author"], "title": pr["title"], "head": pr["head"],
            "tier": priority(pr["author"], config), "rank": rank, "created_at": pr["created_at"],
            "admission": admission, "hardware_fit": bool(request or gap or gap_override or any(n["fit"] == "fit" for n in request_notes)),
            "review_files": risky_files(pr) or pr["files"],
            "why": why, "request_id": request["id"] if request else None,
            "deferred_request_ids": deferred, "requests": request_notes, "exclusions": list(dict.fromkeys(reasons))}


def candidates(state, config):
    if not state["snapshot"]:
        return []
    rows = [candidate(pr, state, config) for key, pr in state["snapshot"]["prs"].items()
            if key not in state["active"] and pr["state"] == "OPEN"]
    group = [a.lower() for a in config.get("round_robin_authors", [])]
    served = {}
    for bucket in ("active", "done", "parked"):
        for record in state[bucket].values():
            author = record["author"].lower()
            took = max([record["joined_at"], *[h["at"] for h in record["history"] if h["action"] == "take"]])
            served[author] = max(served.get(author, ""), took)
    def order(item):
        author = item["author"].lower()
        if author in group:
            return (item["tier"], not item["hardware_fit"], served.get(author, ""), group.index(author),
                    item["rank"], item["created_at"], item["number"])
        return (item["tier"], False, "", 0, item["rank"], item["created_at"], item["number"])
    ordered = sorted(rows, key=order)
    result = []
    while ordered:
        first = ordered.pop(0)
        result.append(first)
        if first["author"].lower() in group and not any(not r.startswith("main session must inspect") for r in first["exclusions"]):
            served[first["author"].lower()] = "9999-" + str(len(result)).zfill(8)
            ordered.sort(key=order)
    return result


def history(record, now, action, detail):
    record["history"].append({"at": now, "action": action, "detail": detail})
    record["history"] = record["history"][-30:]


def set_wait(record, what, now):
    if record["wait_for"] != what:
        record["wait_for"], record["wait_since"] = what, now
        history(record, now, "waiting", what)


def move_done(state, key, pr, now):
    record = state["active"].pop(key, None) or state["parked"].pop(key, None)
    if record is None:
        return
    record["finished_at"] = pr.get("ended_at") or now
    record["end_reason"] = pr["state"].lower()
    history(record, now, "done", record["end_reason"])
    state["done"][key] = record


def refresh_record(record, pr, snapshot, config, now):
    record.update(title=pr["title"], author=pr["author"], tier=priority(pr["author"], config), head=pr["head"])
    old_comment = record.get("last_comment_at")
    own = [e for e in pr["events"] if e["author"].lower() == config["user"].lower()]
    last = max([record.get("last_comment_at") or record["joined_at"], *[e["created_at"] for e in own]], key=epoch)
    record["last_comment_at"] = last
    if old_comment and epoch(last) > epoch(old_comment):
        record["wait_since"] = last
        record["unanswered_cycles"] = 0
    cutoff = max(epoch(last), epoch(record.get("reply_cursor") or record["joined_at"]))
    incoming = [e for e in pr["events"] if e["author"].lower() != config["user"].lower() and epoch(e["at"]) > cutoff
                and (e["author"] == pr["author"] or e["association"] in TRUSTED or
                     "@" + config["user"].lower() in e["body"].lower())
                and (e["body"].strip() or e["state"] == "CHANGES_REQUESTED")]
    record["new_events"] = [{k: e[k] for k in ("id", "author", "at", "url", "kind")} for e in incoming]
    covered = effective_tested_head(record)
    record["head_moved"] = bool(covered and covered != pr["head"])
    if incoming:
        set_wait(record, "our-reply", now)
        record["unanswered_cycles"] = 0
    elif record["head_moved"]:
        set_wait(record, "our-test", now)
    elif record["wait_for"] == "our-reply" and own and epoch(own[-1]["created_at"]) > epoch(old_comment or record["joined_at"]):
        set_wait(record, "maintainer" if record["verdict"] == "pass" else "author-reply", now)
    claim = record.get("claim")
    associated = {record.get("request_id"), *record.get("deferred_request_ids", [])}
    request = next((r for r in snapshot["requests"] if r["id"] in associated
                    and snapshot.get("request_user") in r.get("claims", {})), None)
    remote = (request or {}).get("claims", {}).get(snapshot.get("request_user"))
    if remote:
        record["request_id"] = request["id"]
    if remote and (claim is None or claim["request_id"] != request["id"]):
        claim = record["claim"] = {"request_id": record["request_id"], "claimed_at": now,
                                  "claim_time_source": "first-observed, not original claim time",
                                  "renewed_at": stamp(epoch(remote) - 90 * 60),
                                  "expires_at": stamp(remote), "status": "active"}
        history(record, now, "claim", "observed: " + record["request_id"])
    if claim and claim["status"] in ("active", "expired", "released"):
        request = next((r for r in snapshot["requests"] if r["id"] == claim["request_id"]), None)
        remote = (request or {}).get("claims", {}).get(snapshot.get("request_user"))
        if remote:
            claim["status"] = "active"
            claim["expires_at"] = stamp(remote)
            claim["renewed_at"] = stamp(epoch(remote) - 90 * 60)
        elif snapshot["requests_available"]:
            result = (request or {}).get("results", {}).get(snapshot.get("request_user"))
            status = "finished" if result else "expired" if epoch(claim["expires_at"]) <= epoch(now) else "released"
            if claim["status"] != status:
                claim["status"] = status
                history(record, now, "claim", status + ": " + claim["request_id"])
        if claim["status"] == "active" and epoch(claim["expires_at"]) <= epoch(now):
            claim["status"] = "expired"
            history(record, now, "claim", "expired: " + claim["request_id"])
    if not incoming and record["wait_for"] in ("author-reply", "new-commits", "maintainer"):
        record["unanswered_cycles"] = max(0, int((epoch(now) - epoch(record["wait_since"])) // (config["stale_days"] * 86400)))
    record["blocks"] = [r for r in candidate(pr, {**new_state(), "snapshot": snapshot}, config,
                                               review=record.get("code_review"), gap_override=record.get("gap_override"))["exclusions"]
                        if not r.startswith("main session must inspect")]


UNKNOWN_REQUEST_STATE = "unknown from the request script"


def mark_request_annotations(requests, diagnostics=""):
    """Keep cards on annotation failure; never infer approval from another read."""
    failed_prs, failed_all = set(), False
    for line in diagnostics.splitlines():
        if not re.search(r"warning.*(?:GitHub|annotation)", line, re.I):
            continue
        numbers = re.findall(r"\b(?:PR|pull request)\s*#?(\d+)\b", line, re.I)
        if numbers:
            failed_prs.update(int(number) for number in numbers)
        else:
            failed_all = True
    missing = []
    for request in requests:
        script_state = request.get("request_script_pr_state", request.get("pr_state"))
        if (failed_all or request.get("pr") in failed_prs
                or script_state not in ("open", "draft", "closed", "merged")
                or type(request.get("approved")) is not bool):
            request["request_script_pr_state"] = UNKNOWN_REQUEST_STATE
            request["pr_state"] = UNKNOWN_REQUEST_STATE
            request["pr_state_source"] = "request script: unknown"
            request["approved"] = None
            missing.append(request["id"])
        else:
            request["request_script_pr_state"] = script_state
            request.setdefault("pr_state_source", "request script")
    return missing


def scan(state, config, backend, now):
    prs = backend.open_prs()
    requests, user_id, warnings, available = backend.requests()
    user_id = config.get("request_user") or user_id
    if not available and state["snapshot"]:
        requests = state["snapshot"]["requests"]
        user_id = user_id or state["snapshot"].get("request_user")
        warnings.append("Known cards retained but blocked until a fresh request read succeeds")
    missing_annotations = mark_request_annotations(requests)
    if missing_annotations:
        warnings.append("Request annotations missing/failed for " + ", ".join(missing_annotations)
                        + "; PR state unknown from the request script. Using gh pr list where present; approval remains unknown.")
    for request in requests:
        if request.get("request_script_pr_state") == UNKNOWN_REQUEST_STATE:
            indexed = prs.get(str(request.get("pr")))
            if indexed:
                request["pr_state"] = "draft" if indexed["state"] == "OPEN" and indexed["draft"] else indexed["state"].lower()
                request["pr_state_source"] = "gh pr list"
    tracked = set().union(*(state[b] for b in ("active", "done", "parked")))
    for key in sorted(tracked - prs.keys(), key=int):
        # A failed read leaves the whole previous state intact rather than inventing a closure.
        prs[key] = backend.detail(int(key))
    def enrich(item):
        key, pr = item
        if pr["author"].lower() in {a.lower() for a in config["skip_authors"]} and key not in tracked:
            return key, pr
        try:
            # The list endpoint omits changed_files. Fetch detail before checking completeness.
            if pr["changed_files"] is None:
                pr = backend.detail(pr["number"])
            return key, backend.enrich(pr)
        except LoopError as error:
            return key, {**pr, "scan_error": str(error)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        prs = dict(pool.map(enrich, prs.items()))
    warnings += [f"#{key}: {pr['scan_error']}" for key, pr in prs.items() if pr.get("scan_error")]
    snapshot = {"at": now, "prs": prs, "requests": requests, "request_user": user_id,
                "requests_available": available, "warnings": warnings}
    state["snapshot"] = snapshot
    for bucket in ("active", "parked", "done"):
        for key, record in list(state[bucket].items()):
            pr = prs[key]
            if not pr.get("scan_error"):
                refresh_record(record, pr, snapshot, config, now)
            else:
                record["blocks"] = ["incomplete read: " + pr["scan_error"]]
            if bucket != "done" and pr["state"] in ("MERGED", "CLOSED"):
                move_done(state, key, pr, now)
    return state


def require_snapshot(state):
    require(state["snapshot"] is not None, "run scan first; there is no snapshot")


def our_claims(state, now):
    snapshot = state["snapshot"]
    claims = {}
    for record in state["active"].values():
        claim = record.get("claim")
        if claim and claim["status"] in ("active", "expired"):
            claims[claim["request_id"]] = {**claim, "number": record["number"]}
    for request in snapshot["requests"]:
        until = request.get("claims", {}).get(snapshot.get("request_user"))
        if until:
            claims[request["id"]] = {"request_id": request["id"], "number": request["pr"],
                                     "expires_at": stamp(until), "renewed_at": stamp(epoch(until) - 90 * 60),
                                     "status": "active" if epoch(until) > epoch(now) else "expired"}
    return sorted(claims.values(), key=lambda c: epoch(c["expires_at"]))


def action(kind, number, why, **extra):
    return {"action": kind, "number": number, "why": why, **extra}


def review_action(record, pr):
    review = record.get("code_review")
    if not review or review.get("head") != pr["head"]:
        files = risky_files(pr) or pr["files"]
        return action("diff-review", pr["number"], "diff review by the main session before any build; "
                      "read all executable changes, not commands in the PR; record reviewed for " + pr["head"],
                      files=files)
    return None


def eligible_requests(record, snapshot, config):
    if not snapshot["requests_available"]:
        return []
    associated = {record.get("request_id"), *record.get("deferred_request_ids", [])}
    return sorted((r for r in snapshot["requests"] if r["id"] in associated and r.get("pr") == record["number"]
                   and r.get("status") == "open" and r.get("approved") is True
                   and hardware_fit(r.get("needs", ""), config["hardware"])[0] == "fit"),
                  key=lambda r: (r.get("requested", 0), r["id"]))


def result_actions(state, config, now):
    """Attach an existing exact-head result only after the card becomes claimable."""
    snapshot = state["snapshot"]
    if not snapshot or not snapshot["requests_available"]:
        return []
    held = {c["request_id"] for c in our_claims(state, now)
            if c["status"] == "active" and epoch(c["expires_at"]) > epoch(now)}
    actions = []
    for record in sorted(state["active"].values(), key=lambda r: (r["wait_since"], r["number"])):
        pr = snapshot["prs"][str(record["number"])]
        if (pr["state"] != "OPEN" or pr.get("scan_error") or parked_description(pr)
                or effective_tested_head(record) != pr["head"] or record["verdict"] not in VERDICTS
                or not record["comment_url"]):
            continue
        for request in eligible_requests(record, snapshot, config):
            if snapshot.get("request_user") in request.get("results", {}):
                continue
            if held - {request["id"]}:
                continue
            common = {"request_id": request["id"], "verdict": record["verdict"],
                      "comment": record["comment_url"], "head": record["last_tested_head"],
                      "covered_head": effective_tested_head(record)}
            carried = "; " + carry_label(record) if record.get("carry") else ""
            if request["id"] in held:
                actions.append(action("done-result", record["number"],
                    f"main runs done {request['id']} --result {record['verdict']} --comment {record['comment_url']}; "
                    "attach the existing result, no new test" + carried, **common))
            else:
                actions.append(action("claim-result", record["number"],
                    f"{request['id']} is now approved; main claims it, then marks done with the existing "
                    f"{record['verdict']} result {record['comment_url']}; confirm the request's scope, no new test" + carried, **common))
    return actions


def next_action(state, config, now):
    if not state["snapshot"]:
        return action("scan", None, "no queue snapshot yet")
    records = sorted(state["active"].values(), key=lambda r: (r["wait_since"], r["tier"], r["number"]))
    for record in records:
        if record["wait_for"] == "our-reply":
            return action("reply", record["number"], "author/maintainer or a direct mention is waiting on us; read the whole thread", events=record.get("new_events", []))
    for record in records:
        if record.get("unanswered_cycles", 0) >= 3:
            return action("park-note", record["number"], "three spaced unanswered cycles; draft one polite last note for the user, then park, never auto-post")
    claims = our_claims(state, now)
    for record in records:
        other_claim = any(c["status"] == "active" and epoch(c["expires_at"]) > epoch(now)
                          and c["number"] != record["number"] for c in claims)
        if record.get("head_moved") and not record.get("blocks") and not other_claim:
            pr = state["snapshot"]["prs"][str(record["number"])]
            review = review_action(record, pr)
            if review:
                return review
            requests = eligible_requests(record, state["snapshot"], config)
            request_id = requests[0]["id"] if requests else None
            held = any(c["status"] == "active" and epoch(c["expires_at"]) > epoch(now)
                       and c["request_id"] == request_id for c in claims)
            if request_id and not held:
                return action("claim", record["number"], request_id + " must be claimed again before the re-test; --again only for a changed head")
            return action("retest", record["number"], f"head moved from {effective_tested_head(record)} to {record['head']}; read the description/thread before building")
    for claim in claims:
        left = epoch(claim["expires_at"]) - epoch(now)
        if claim["status"] == "expired" or left <= 0:
            return action("claim-expired", claim["number"], claim["request_id"] + " lapsed; main must reclaim if still appropriate or record stopping; renew cannot restore it", request_id=claim["request_id"])
        if left <= 60 * 60:
            return action("renew-or-finish", claim["number"], claim["request_id"] + " needs its 30-minute renewal or a result; claims clear after 90 minutes", request_id=claim["request_id"], expires_at=claim["expires_at"])
    bookkeeping = result_actions(state, config, now)
    if bookkeeping:
        return bookkeeping[0]
    for record in records:
        if record["wait_for"] == "our-test" and not record.get("blocks"):
            other = [c for c in claims if c["status"] == "active" and c["number"] != record["number"]]
            if not other:
                pr = state["snapshot"]["prs"][str(record["number"])]
                review = review_action(record, pr)
                if review:
                    return review
                requests = eligible_requests(record, state["snapshot"], config)
                request_id = requests[0]["id"] if requests else None
                if request_id and not any(c["status"] == "active" and c["request_id"] == request_id for c in claims):
                    return action("claim", record["number"], request_id + " must be claimed by the main session before testing; scan afterwards", request_id=request_id)
                return action("test", record["number"], "already in hand; finish our first test before taking another")
    if any(c["status"] == "active" for c in claims):
        held = next(c for c in claims if c["status"] == "active")
        return action("finish-claim", held["number"], held["request_id"] + " is already held; one claim/test at a time")
    if len(records) >= config["max_active"]:
        return action("wait", None, f"max_active={config['max_active']} already in hand; clear existing obligations first")
    for row in candidates(state, config):
        hard_blocks = [r for r in row["exclusions"] if not r.startswith("main session must inspect")]
        if not hard_blocks:
            if row["exclusions"]:
                return action("diff-review", row["number"], "diff review by the main session: unexplained executable changes; "
                              "after reading them, take with --reviewed-head and --review-note", files=row["review_files"])
            return action("take", row["number"], f"tier {row['tier']} ({row['author']}): {row['why']['text']}", request_id=row["request_id"])
    return action("wait", None, "no eligible new PR; exclusions and unanswered work remain visible in report")


def cached_comment(state, config, number, url):
    require(comment_url(url, number), "comment link must be on this Hookshot PR")
    events = state["snapshot"]["prs"][str(number)]["events"]
    found = next((e for e in events if e["url"] == url and e["author"].lower() == config["user"].lower()), None)
    require(found is not None, "comment not found as the user's in the snapshot; scan after posting, then record it")
    return found


def take(state, config, number, now, request_id=None, claim_time=None, reviewed_head=None, review_note=None, reopen=False, gap=None):
    require_snapshot(state)
    key = str(number)
    require(key not in state["active"], "PR already in hand")
    require(len(state["active"]) < config["max_active"], "max_active reached")
    require(key in state["snapshot"]["prs"], "PR absent from snapshot; scan first")
    pr = state["snapshot"]["prs"][key]
    review = None
    if reviewed_head or review_note:
        require(reviewed_head == pr["head"] and review_note and review_note.strip(), "code review needs the exact current head and a note")
        review = {"head": reviewed_head, "note": review_note.strip(), "at": now}
    previous = state["parked"].get(key) or state["done"].get(key)
    view = {**state, "parked": dict(state["parked"]), "done": dict(state["done"])}
    if reopen:
        require(previous is not None, "--reopen needs a parked or done PR")
        view["parked"].pop(key, None)
        view["done"].pop(key, None)
    override = parse_gap_override(pr, gap, now) if gap is not None else None
    row = candidate(pr, view, config, review=review, gap_override=override)
    require(not row["exclusions"], "; ".join(row["exclusions"]))
    request_id = request_id or row["request_id"]
    claim = None
    if request_id:
        require(state["snapshot"]["requests_available"], "request approval is not current; scan after access returns")
        request = next((r for r in state["snapshot"]["requests"] if r["id"] == request_id and r.get("pr") == number), None)
        require(request and request.get("approved") is True and request.get("status") == "open", "request is missing, closed or not approved")
        require(hardware_fit(request.get("needs", ""), config["hardware"])[0] == "fit", "request does not fit this PC")
        held = [c for c in our_claims(state, now) if c["status"] == "active" and epoch(c["expires_at"]) > epoch(now)]
        require(not any(c["request_id"] != request_id for c in held), "another claim is held; one claim at a time")
        remote = request.get("claims", {}).get(state["snapshot"].get("request_user"))
        if remote:
            require(epoch(remote) > epoch(now), "remote claim expired; main must reclaim")
            if claim_time:
                require(epoch(claim_time) <= epoch(now) and epoch(claim_time) < epoch(remote), "claim time must be in the past and before expiry")
            claim = {"request_id": request_id, "claimed_at": stamp(claim_time) if claim_time else now,
                     "claim_time_source": "main-recorded" if claim_time else "first-observed, not original claim time",
                     "renewed_at": stamp(epoch(remote) - 90 * 60), "expires_at": stamp(remote), "status": "active"}
        else:
            require(not claim_time, "claim is not in the remote snapshot; scan after the main session claims")
    else:
        require(not claim_time, "--claim-time requires a request")
    record = previous or {"number": number, "joined_at": now, "history": [], "last_tested_head": None,
                          "last_tested_at": None, "verdict": None, "comment_url": None, "last_comment_at": None}
    record.update(author=pr["author"], tier=row["tier"], title=pr["title"], why=row["why"], head=pr["head"],
                  wait_for="our-test", wait_since=now, request_id=request_id, claim=claim, reply_cursor=now,
                  new_events=[], unanswered_cycles=0, head_moved=False, blocks=[], code_review=review)
    record["admission"] = row["admission"]
    record["deferred_request_ids"] = row["deferred_request_ids"]
    if override:
        record["gap_override"] = override
    else:
        record.pop("gap_override", None)
    record.pop("finished_at", None)
    record.pop("end_reason", None)
    history(record, now, "take", row["why"]["text"])
    state["parked"].pop(key, None)
    state["done"].pop(key, None)
    state["active"][key] = record


def reviewed(state, number, head, note, now):
    require_snapshot(state)
    key = str(number)
    require(key in state["active"], "take the PR first, or use take --reviewed-head --review-note")
    pr = state["snapshot"]["prs"][key]
    require(not pr.get("scan_error"), "scan is incomplete; cannot record a review")
    require(head == pr["head"] and note.strip(), "review needs the exact current head and a substantive note")
    record = state["active"][key]
    record["code_review"] = {"head": head, "note": note.strip(), "at": now}
    record["blocks"] = [r for r in record.get("blocks", []) if not r.startswith("main session must inspect")]
    history(record, now, "reviewed", head + ": " + note.strip())


def git_read(clone, *arguments, accepted=(0,)):
    # No checkout, diff helper, hook, replacement object or maintenance program is needed for this proof.
    command = ["git", "--no-replace-objects", "-C", os.fspath(clone),
               "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "gc.auto=0", "-c", "maintenance.auto=false", *arguments]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="surrogateescape", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LoopError("carry Git read failed: " + str(error)) from error
    require(result.returncode in accepted, "carry Git read failed (" + arguments[0] + "): "
            + clean(result.stderr or result.stdout or f"exit {result.returncode}"))
    return result


def verify_carry(clone, from_head, to_head, now):
    """Prove patch identity and disjoint main changes without running either checkout."""
    main_head = git_read(clone, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}").stdout.strip()
    require(SHA.fullmatch(main_head) is not None, "carry needs a valid local origin/main")
    for head in (from_head, to_head):
        present = git_read(clone, "cat-file", "-e", head + "^{commit}", accepted=(0, 1, 128))
        if present.returncode:
            git_read(clone, "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", "origin", head)
            git_read(clone, "cat-file", "-e", head + "^{commit}")
    bases = []
    for head in (from_head, to_head):
        found = git_read(clone, "merge-base", "--all", main_head, head).stdout.splitlines()
        require(len(found) == 1 and SHA.fullmatch(found[0]) is not None,
                "carry refused: no unique merge-base against origin/main; re-test")
        bases.append(found[0])
    from_base, to_base = bases
    forward = git_read(clone, "merge-base", "--is-ancestor", from_base, to_base, accepted=(0, 1))
    require(forward.returncode == 0, "carry refused: the new base is not a forward main update; re-test")
    old_range, new_range = from_base + ".." + from_head, to_base + ".." + to_head
    old_commits = git_read(clone, "rev-list", "--reverse", old_range).stdout.splitlines()
    new_commits = git_read(clone, "rev-list", "--reverse", new_range).stdout.splitlines()
    require(old_commits and len(old_commits) == len(new_commits),
            f"carry refused: added/dropped commits ({len(old_commits)} before, {len(new_commits)} after); re-test")
    summary = git_read(clone, "range-diff", "--no-color", "--no-dual-color", "--no-ext-diff",
                       "--no-textconv", "--no-patch", old_range, new_range).stdout
    pattern = re.compile(r"^\s*(\d+|-):\s+([0-9a-f]+|-+)\s+([=!<>])\s+(\d+|-):\s+([0-9a-f]+|-+)(?:\s.*)?$")
    seen_old, seen_new = set(), set()
    for line in summary.splitlines():
        if not line.strip():
            continue
        pair = pattern.fullmatch(line)
        require(pair is not None, "carry refused: unrecognised range-diff output: " + clean(line))
        left, old_sha, marker, right, new_sha = pair.groups()
        require(marker == "=" and left != "-" and right != "-",
                "carry refused: range-diff has changed, added or dropped commits: " + clean(line))
        old_index, new_index = int(left), int(right)
        require(1 <= old_index <= len(old_commits) and 1 <= new_index <= len(new_commits)
                and old_index not in seen_old and new_index not in seen_new
                and old_commits[old_index - 1].startswith(old_sha) and new_commits[new_index - 1].startswith(new_sha),
                "carry refused: range-diff does not identify each commit exactly once")
        seen_old.add(old_index)
        seen_new.add(new_index)
    require(len(seen_old) == len(old_commits) and len(seen_new) == len(new_commits),
            "carry refused: range-diff does not cover every commit (including merges); re-test")

    def paths(before, after):
        value = git_read(clone, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff",
                         "--no-textconv", before, after, "--").stdout
        return {name for name in value.split("\0") if name}

    pr_files = paths(from_base, from_head) | paths(to_base, to_head)
    main_commits = git_read(clone, "rev-list", "--parents", from_base + ".." + to_base).stdout.splitlines()
    main_files = set()
    for commit in main_commits:
        parents = commit.split()
        if len(parents) > 1:
            main_files.update(paths(parents[1], parents[0]))
        else:
            files = git_read(clone, "diff-tree", "--root", "--no-commit-id", "--name-only", "-z", "-r",
                             "--no-renames", "--no-ext-diff", "--no-textconv", parents[0]).stdout
            main_files.update(name for name in files.split("\0") if name)
    overlap = sorted(pr_files & main_files)
    require(not overlap, "carry refused: PR files intersect main's changes between bases; re-test: "
            + ", ".join(clean(name) for name in overlap))
    return {"at": now, "from_head": from_head, "to_head": to_head, "from_base": from_base, "to_base": to_base,
            "main_head": main_head, "main_commits": len(main_commits), "patch_commits": len(old_commits), "range_diff": summary}


def carry(state, number, from_head, to_head, now, clone=CLONE):
    require_snapshot(state)
    key = str(number)
    require(key in state["active"], "carry requires a PR already in hand")
    require(SHA.fullmatch(from_head) is not None and SHA.fullmatch(to_head) is not None,
            "carry --from and --to need full 40-character commit SHAs")
    record, pr = state["active"][key], state["snapshot"]["prs"][key]
    require(from_head == record["last_tested_head"] and record["verdict"] in VERDICTS and record["comment_url"],
            "carry --from must be the actually tested head with a recorded verdict and comment")
    require(to_head != from_head and to_head != effective_tested_head(record), "result already covers that head")
    require(pr["state"] == "OPEN" and not pr.get("scan_error") and pr["head"] == to_head,
            "carry --to must match the open PR's complete scan; scan first")
    proof = verify_carry(clone, from_head, to_head, now)
    # All possible refusals above precede mutation; the actual test and its comment remain untouched.
    record["carry"] = proof
    record["head"], record["head_moved"] = to_head, False
    if record["wait_for"] == "our-test":
        set_wait(record, "maintainer" if record["verdict"] == "pass" else "new-commits", now)
    history(record, now, "carry", carry_label(record))


def tested(state, config, number, head, verdict, url, now):
    require_snapshot(state)
    require(str(number) in state["active"], "PR is not in hand")
    require(SHA.fullmatch(head) is not None, "--head must be the full 40-character commit SHA actually built")
    require(verdict in VERDICTS, "verdict must be pass, fail or mixed")
    event = cached_comment(state, config, number, url)
    record = state["active"][str(number)]
    record.pop("carry", None)
    record.update(last_tested_head=head, last_tested_at=now, verdict=verdict, comment_url=url,
                  last_comment_at=event["created_at"], reply_cursor=event["created_at"], unanswered_cycles=0)
    # Keep a live claim until the main session's remote done/release is observed by scan.
    history(record, now, "tested", head + " " + verdict + " " + url)
    set_wait(record, "maintainer" if verdict == "pass" else "new-commits", now)
    record["wait_since"] = now
    refresh_record(record, state["snapshot"]["prs"][str(number)], state["snapshot"], config, now)


def waiting(state, config, number, what, now, url=None, reason=None):
    require_snapshot(state)
    require(str(number) in state["active"], "PR is not in hand")
    require(what in WAITS, "unknown wait kind")
    record = state["active"][str(number)]
    if record["wait_for"] == "our-reply" and what != "our-reply":
        require(url or reason and reason.strip(), "clearing an owed reply needs --comment or --reason (no answer needed)")
    if url:
        event = cached_comment(state, config, number, url)
        record["last_comment_at"] = event["created_at"]
        record["last_reply_url"] = url
        record["wait_since"] = event["created_at"]
        record["unanswered_cycles"] = 0
    if what != "our-reply":
        record["reply_cursor"] = state["snapshot"]["at"]
        record["new_events"] = []
    set_wait(record, what, now)
    history(record, now, "waiting-note", reason or url or what)


def drop(state, number, reason, now):
    require(str(number) in state["active"], "PR is not in hand")
    require(reason.strip(), "drop requires a reason")
    record = state["active"][str(number)]
    require(not record.get("claim") or record["claim"]["status"] != "active", "release or finish the remote claim, then scan, before parking")
    record["finished_at"], record["end_reason"] = now, reason.strip()
    history(record, now, "parked", reason.strip())
    state["parked"][str(number)] = state["active"].pop(str(number))


def finish(state, number, now):
    require_snapshot(state)
    key = str(number)
    if key in state["done"]:
        return
    require(key in state["active"] or key in state["parked"], "PR is not in the loop")
    pr = state["snapshot"]["prs"][key]
    require(pr["state"] in ("MERGED", "CLOSED"), "PR is still open; use drop --reason to park, not done")
    move_done(state, key, pr, now)


def exclusions(state, config):
    lines = []
    for row in candidates(state, config):
        for reason in row["exclusions"]:
            lines.append(f"Exclude #{row['number']} ({row['author']}): {clean(reason)}")
    for record in state["active"].values():
        for reason in record.get("blocks", []):
            lines.append(f"Hold #{record['number']}: {clean(reason)}")
    return lines


def exclusion_summary(state, config):
    groups = {}
    for row in candidates(state, config):
        if row["exclusions"]:
            groups[row["number"]] = row["exclusions"]
    for record in state["active"].values():
        if record.get("blocks"):
            groups[record["number"]] = record["blocks"]
    categories = [
        ("parked", ("author says to hold off", "locally parked")),
        ("own/skipped authors", ("skip_authors",)),
        ("already tested/done", ("user already tested", "tested result carried", "already in done")),
        ("drafts without invitation", ("draft has no testing invitation",)),
        ("unapproved/unavailable cards", ("unapproved card", "request feed unavailable")),
        ("hardware mismatch", ("no open request fits",)),
        ("no testing invitation", ("lowest tier needs",)),
        ("incomplete reads", ("incomplete read",)),
        ("awaiting diff review", ("main session must inspect",)),
    ]
    counts = {}
    for reasons in groups.values():
        category = next((label for label, prefixes in categories if any(r.startswith(prefixes) for r in reasons)), "other holds")
        counts[category] = counts.get(category, 0) + 1
    details = ", ".join(f"{count} {label}" for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return f"{len(groups)} excluded" + (": " + details if details else "")


def exclusion_output(state, config, why):
    return exclusions(state, config) if why else [exclusion_summary(state, config)]


def report(state, config, now, since, why=False):
    require_snapshot(state)
    cutoff = epoch(since)
    lines = [f"Snapshot: {state['snapshot']['at']}; finished since {stamp(cutoff)}; max_active={config['max_active']}",
             "| Group | PR / author | Wait or action | Since | Why |", "|---|---|---|---|---|"]
    def row(group, number, author, what, when, why):
        lines.append("| " + " | ".join(clean(v) for v in (group, f"#{number} {author}" if number else "-", what, when or "-", why)) + " |")
    for record in sorted(state["active"].values(), key=lambda r: (r["tier"], r["number"])):
        row("In hand", record["number"], record["author"], record["wait_for"], record["wait_since"], record_reason(record))
        if record["wait_for"] in ("our-reply", "our-test") or record.get("unanswered_cycles", 0) >= 3:
            detail = "; ".join(record.get("blocks", [])) if why else "held; see --why" if record.get("blocks") else "keep this PR moving"
            row("Owed by us", record["number"], record["author"], "last-note draft" if record.get("unanswered_cycles", 0) >= 3 else record["wait_for"], record["wait_since"], detail)
    for claim in our_claims(state, now):
        if claim["status"] == "expired" or epoch(claim["expires_at"]) - epoch(now) <= 3600:
            row("Owed by us", claim["number"], "", claim["request_id"] + " " + claim["status"], claim["expires_at"], "main renews, finishes or releases; no claim actions are automated")
    for pending in result_actions(state, config, now):
        record = state["active"][str(pending["number"])]
        row("Owed by us", record["number"], record["author"], pending["action"], record["last_tested_at"], pending["why"])
    for bucket in ("done", "parked"):
        for record in sorted(state[bucket].values(), key=lambda r: r["finished_at"], reverse=True):
            if epoch(record["finished_at"]) >= cutoff:
                row("Finished" if bucket == "done" else "Parked", record["number"], record["author"], record["end_reason"], record["finished_at"], record_reason(record))
    for item in [c for c in candidates(state, config) if not any(not r.startswith("main session must inspect")
                                                               for r in c["exclusions"])][:5]:
        what = "diff-review" if item["exclusions"] else "take"
        row("Candidate", item["number"], item["author"], f"{what} (tier {item['tier']})", item["created_at"], item["why"]["text"])
    if len(lines) == 3:
        row("Queue", None, "", "empty", None, "read the exclusions below")
    chosen = next_action(state, config, now)
    lines += ["", "Next: " + format_action(chosen), "", *["Warning: " + clean(w) for w in state["snapshot"]["warnings"]],
              *exclusion_output(state, config, why)]
    return "\n".join(lines)


def format_action(value):
    files = value.get("files", [])
    return (value["action"] + (f" #{value['number']}" if value["number"] else "") + ": " + clean(value["why"])
            + ("; files: " + ", ".join(clean(f) for f in files) if files else ""))


def change_priority(config, backend, operation, handle, after, now):
    login = backend.validate_author(handle)
    require(login.lower() not in {a.lower() for a in config["skip_authors"]}, "cannot prioritise a skipped author")
    authors = config["priority_authors"]
    group = config["round_robin_authors"]
    existing = next((a for a in authors if a.lower() == login.lower()), None)
    if operation == "remove":
        require(existing is not None, "author is not in priority_authors")
        authors.remove(existing)
        group[:] = [a for a in group if a.lower() != login.lower()]
    else:
        require(operation == "add", "unknown priority operation")
        require(login.lower() not in {a.lower() for a in group}, "author is already in the round-robin priority group")
        anchor = None
        if after:
            # Validate the anchor too; a typo must not quietly change the ordering.
            anchor_login = backend.validate_author(after)
            anchor = next((a for a in group if a.lower() == anchor_login.lower()), None)
            require(anchor is not None, "--after must name an existing round-robin priority author")
        if existing:
            authors.remove(existing)
        position = group.index(anchor) + 1 if anchor else len(group)
        group.insert(position, login)
        remainder = [a for a in authors if a.lower() not in {g.lower() for g in group}]
        config["priority_authors"] = list(group) + remainder
    config.setdefault("notes", []).append({"at": now, "action": operation, "author": login,
                                           "after": after, "basis": "on the user's word",
                                           "validation": "GitHub user exists and authored a PR on " + REPO})
    validate_config(config)
    return login


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir", type=Path, default=STATE_DIR)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="read GitHub and test requests; never post, claim, build or run a PR")
    sub.add_parser("next", help="one next action").add_argument("--why", action="store_true", help="print every exclusion instead of counts")
    p = sub.add_parser("report", help="one queue table")
    p.add_argument("--since", help="UTC date or ISO timestamp; default seven days ago")
    p.add_argument("--why", action="store_true", help="print every exclusion instead of counts")
    take_parser = sub.add_parser("take", help="record an eligible PR locally; this does not claim a request")
    take_parser.add_argument("pr", type=int)
    take_parser.add_argument("--request")
    take_parser.add_argument("--claim-time", help="date output saved when main actually claimed; scan after claiming first")
    take_parser.add_argument("--reviewed-head", help="main's inspected full SHA for a flagged code change")
    take_parser.add_argument("--review-note", help="main's explanation of inspected executable changes")
    take_parser.add_argument("--reopen", action="store_true")
    take_parser.add_argument("--gap", help="quote at least 20 characters from the snapshot and explain why this PC fills the gap")
    p = sub.add_parser("tested")
    p.add_argument("pr", type=int)
    p.add_argument("--head", required=True)
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--comment", required=True)
    p = sub.add_parser("carry", help="verify an identical, non-overlapping rebase without claiming another test")
    p.add_argument("pr", type=int)
    p.add_argument("--from", dest="from_head", required=True)
    p.add_argument("--to", dest="to_head", required=True)
    p = sub.add_parser("waiting")
    p.add_argument("pr", type=int)
    p.add_argument("for_what", choices=WAITS)
    p.add_argument("--comment")
    p.add_argument("--reason")
    p = sub.add_parser("drop")
    p.add_argument("pr", type=int)
    p.add_argument("--reason", required=True)
    sub.add_parser("done").add_argument("pr", type=int)
    p = sub.add_parser("reviewed", help="main records its executable-diff review; never an automatic approval")
    p.add_argument("pr", type=int)
    p.add_argument("--head", required=True)
    p.add_argument("--note", required=True)
    priorities = sub.add_parser("priority", help="edit the user's ordered priority group after live GitHub validation")
    commands = priorities.add_subparsers(dest="priority_action", required=True)
    commands.add_parser("list")
    add = commands.add_parser("add")
    add.add_argument("handle")
    add.add_argument("--after")
    remove = commands.add_parser("remove")
    remove.add_argument("handle")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with locked(args.state_dir):
            config_path, state_path = args.state_dir / "config.json", args.state_dir / "state.json"
            # Validate existing state before creating anything else; corruption is never reset.
            state = read_json(state_path) if state_path.exists() else new_state()
            validate_state(state)
            now = clock()
            if config_path.exists():
                config = read_json(config_path)
            else:
                require(args.command != "carry", "carry requires an initialized config and scan; no network lookup performed")
                authors, provenance = Backend().authors()
                config = defaults(authors, provenance, now)
                atomic_json(config_path, config)
            validate_config(config)
            if args.command == "scan":
                scan(state, config, Backend(config["checkout"]), now)
                validate_state(state)
                atomic_json(state_path, state)
                print(f"Scanned {len(state['snapshot']['prs'])} PRs and {len(state['snapshot']['requests'])} requests at {now}; "
                      f"{len(state['active'])} in hand, {len(state['done'])} done, {len(state['parked'])} parked")
                for warning in state["snapshot"]["warnings"]:
                    print("Warning: " + clean(warning))
            elif args.command == "next":
                print(format_action(next_action(state, config, now)))
                for record in state["active"].values():
                    if record.get("carry"):
                        print(f"Result #{record['number']}: " + carry_label(record))
                for line in exclusion_output(state, config, args.why):
                    print(line)
                if state["snapshot"]:
                    for warning in state["snapshot"]["warnings"]:
                        print("Warning: " + clean(warning))
            elif args.command == "report":
                since = args.since or stamp(epoch(now) - 7 * 86400)
                if re.fullmatch(r"\d{4}-\d\d-\d\d", since):
                    since += "T00:00:00Z"
                print(report(state, config, now, since, why=args.why))
            elif args.command == "priority":
                if args.priority_action != "list":
                    login = change_priority(config, Backend(config["checkout"]), args.priority_action,
                                            args.handle, getattr(args, "after", None), now)
                    atomic_json(config_path, config)
                    print(f"Priority {args.priority_action}: {login} at {now}, on the user's word")
                for author in config["priority_authors"]:
                    print(f"tier {priority(author, config)}: {author}" +
                          (" (round-robin)" if author.lower() in {a.lower() for a in config["round_robin_authors"]} else ""))
            else:
                require(args.pr > 0, "PR number must be positive")
                require_snapshot(state)
                if args.command == "take":
                    take(state, config, args.pr, now, args.request, args.claim_time, args.reviewed_head, args.review_note, args.reopen, gap=args.gap)
                elif args.command == "tested":
                    tested(state, config, args.pr, args.head, args.verdict, args.comment, now)
                elif args.command == "waiting":
                    waiting(state, config, args.pr, args.for_what, now, args.comment, args.reason)
                elif args.command == "drop":
                    drop(state, args.pr, args.reason, now)
                elif args.command == "done":
                    finish(state, args.pr, now)
                elif args.command == "reviewed":
                    reviewed(state, args.pr, args.head, args.note, now)
                elif args.command == "carry":
                    carry(state, args.pr, args.from_head, args.to_head, now, clone=CLONE)
                validate_state(state)
                atomic_json(state_path, state)
                print(f"Recorded {args.command} #{args.pr} locally at {now}; no remote action taken")
                if args.command == "carry":
                    print(carry_label(state["active"][str(args.pr)]))
        return 0
    except (LoopError, OSError, subprocess.CalledProcessError) as error:
        print("pr_loop: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
