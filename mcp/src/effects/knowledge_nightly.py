"""Read retained rolling files and SQLite in mode=ro; never import its mutating Store."""
from contextlib import closing
import re
import sqlite3
from datetime import datetime, timezone
from src.models import utc_now
from src.effects.knowledge_common import (checked_path, config, digest, optional_json, read_bytes,
    sha, stamp, text, under)
from src.effects.knowledge_documents import render_section


def valid_cycle(deps, cycle):
    if not isinstance(cycle, str) or not re.fullmatch(r"(?:[0-9]{8}T[0-9]{6}|baseline-[0-9a-f]{8})", cycle):
        raise deps.error("invalid_argument", "nightly_status")
    return cycle


def build_metadata(value):
    value = value if isinstance(value, dict) else {}
    return {"commit": sha(value.get("commit")), "source_stamp": sha(value.get("source_stamp")),
        "started": stamp(value.get("started")), "cycle": text(value.get("cycle"), 80).model_dump(),
        "build_id": text(value.get("build_id"), 80).model_dump()}


def nightly_status_impl(deps, cycle=None):
    cfg = config(deps)
    root = checked_path(deps, cfg.nightly_root, "nightly_status", directory=True)
    state = optional_json(deps, root, "state.json", "nightly_status") or {}
    latest = optional_json(deps, root, "latest.json", "nightly_status") or {}
    baseline = optional_json(deps, root, "release-baseline.json", "nightly_status")
    selected = cycle if cycle is not None else latest.get("cycle")
    if selected is not None:
        valid_cycle(deps, selected)
    relative = ("baselines/" if selected and selected.startswith("baseline-") else "raw/") + str(selected)
    record = optional_json(deps, root, relative + "/cycle.json", "nightly_status") if selected else None
    limitations = ["Watch ownership and queued jobs are not proven by retained files.",
        "Retained status does not start or resume a cycle; missing evidence remains unknown."]
    last_complete_db = None
    database_cycle = None
    if cfg.nightly_database is not None:
        path = checked_path(deps, cfg.nightly_database, "nightly_status")
        # mode=ro refuses a missing database and never creates schema or WAL settings.
        # Do not use immutable=1: rolling writers can have newer committed WAL pages.
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                deadline = datetime.now(timezone.utc).timestamp() + 1
                db.set_progress_handler(lambda: 1 if datetime.now(timezone.utc).timestamp() > deadline else 0, 1000)
                last = db.execute("SELECT id,commit_sha,started,ended,status FROM nights WHERE ended IS NOT NULL AND status IN ('complete','complete-unjudged','baseline-complete') ORDER BY ended DESC LIMIT 1").fetchone()
                if last:
                    last_complete_db = {"id": text(last["id"], 80).model_dump(), "commit": sha(last["commit_sha"]),
                        "started": stamp(last["started"]), "ended": stamp(last["ended"]), "status": text(last["status"], 80).model_dump()}
                if selected:
                    row = db.execute("SELECT status,ended FROM nights WHERE id=?", ("rolling-" + selected,)).fetchone()
                    if row:
                        database_cycle = {"stage": text(row["status"], 80).model_dump(), "ended": stamp(row["ended"])}
        except sqlite3.Error:
            limitations.append("Read-only database observation unavailable; no schema was initialized.")
    else:
        limitations.append("Read-only database is not configured.")
    if not state and not latest and not record and last_complete_db is None:
        raise deps.error("unavailable", "nightly_status")
    draft = optional_json(deps, root, "outbox/" + selected + ".json", "nightly_status") if selected else None
    if record and record.get("error"):
        limitations.append("Cycle recorded a failure; raw local diagnostics are not exposed.")
    baseline_at = stamp(baseline.get("measured_at")) if baseline else None
    age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(baseline_at.replace("Z", "+00:00"))).total_seconds()) if baseline_at else None
    return {"observed_at": utc_now(), "watch_owner": None, "cycle": selected,
        "stage": text((record or {}).get("status") or (latest.get("status") if latest.get("cycle") == selected else None), 80).model_dump(),
        "last_complete": last_complete_db, "last_completed_state": build_metadata(state.get("last_completed")),
        "database_cycle": database_cycle, "build": build_metadata((record or {}).get("build")),
        "baseline": {"complete": baseline.get("complete") if type(baseline.get("complete")) is bool else None,
            "measured_at": baseline_at, "age_seconds": age, "build": build_metadata(baseline.get("build"))} if baseline else None,
        "queued_jobs": None, "draft_events": [{"cycle": selected, "status": text(draft.get("status"), 80).model_dump(), "posted": None}] if draft else [],
        "limitations": limitations, "privacy": "private", "untrusted": True}


def nightly_resource_impl(deps, cycle, start=1, limit=40, revision=None):
    valid_cycle(deps, cycle)
    status = nightly_status_impl(deps, cycle)
    try:
        path = under(deps, config(deps).nightly_root, "reports/" + cycle + ".md", "nightly_status")
    except Exception as error:
        if getattr(getattr(error, "envelope", None), "code", None) == "not_found":
            return {"status": status, "report": None, "limitations": ["Retained report unavailable."]}
        raise
    raw = read_bytes(deps, path, "nightly_status")
    actual = digest(raw)
    if revision is not None and revision != actual:
        raise deps.error("busy", "nightly_status")
    report = render_section(deps, raw.decode("utf-8").splitlines(), actual, "private", "workbench://nightly/" + cycle, start, limit)
    return {"status": status, "report": report.model_dump()}
