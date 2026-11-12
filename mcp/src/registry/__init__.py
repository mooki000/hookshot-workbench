"""SQLite WAL registry with short transactions and immutable attempt identities."""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.errors import fail
from src.models import (Artifact, Event, EventPage, Job, JobPage, ProcessIdentity,
                        Producer, Receipt, Run, State, TERMINAL, utc_now)

MAX_PAGE = 100
MAX_EVENT_BYTES = 16384
TRANSITIONS = {
    State.PLANNED: {State.QUEUED, State.CANCELLED, State.FAILED},
    State.QUEUED: {State.STARTING, State.RUNNING, State.STOPPING, State.CANCELLED, State.FAILED, State.INTERRUPTED},
    State.STARTING: {State.RUNNING, State.STOPPING, State.FAILED, State.CANCELLED, State.INTERRUPTED},
    State.RUNNING: {State.STOPPING, State.COMPLETED, State.FAILED, State.CANCELLED, State.INTERRUPTED},
    State.STOPPING: {State.COMPLETED, State.FAILED, State.CANCELLED, State.INTERRUPTED},
}


def new_id(kind: str) -> str:
    return kind + "_" + uuid.uuid4().hex


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _valid_id(value: str, kind: str) -> None:
    if not re.fullmatch(kind + r"_[0-9a-f]{32}", value):
        raise fail("invalid_argument", "registry_read")


def _page(limit: int, cursor: int) -> None:
    if not 1 <= limit <= MAX_PAGE or not 0 <= cursor <= 9223372036854775807:
        raise fail("invalid_argument", "registry_read")


class Registry:
    def __init__(self, state_root: Path):
        self.state_root = state_root
        self.path = state_root / "registry.sqlite3"
        self.owner: str | None = None

    @contextmanager
    def _db(self, *, write: bool = False):
        if self.owner is None:
            raise fail("unavailable", "registry")
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except sqlite3.OperationalError as exc:
            db.rollback()
            busy = getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            raise fail("busy" if busy else "backend_failed", "registry", retryable=busy) from None
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def open(self) -> None:
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # SQLite's journal-mode transition can fail immediately under concurrent
        # first opens despite busy_timeout. Serialize only registry initialization.
        fd = os.open(self.state_root / "registry.init.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise fail("busy", "registry", retryable=True) from None
                    time.sleep(0.02)
            self._initialize()
        finally:
            os.close(fd)

    def _initialize(self) -> None:
        # Never follow a pre-planted registry link.
        if self.path.is_symlink():
            raise fail("refused_by_policy", "registry")
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    owner TEXT NOT NULL, operation TEXT NOT NULL, idem TEXT,
                    request_hash TEXT NOT NULL, document TEXT NOT NULL,
                    process TEXT, supervisor TEXT, receipt TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(owner, operation, idem));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    observed_at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, seq);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_owners (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id));
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, path TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('owner', ?)", (new_id("owner"),))
            self.owner = db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()[0]
            db.commit()
        finally:
            db.close()
        self.path.chmod(0o600)

    def close(self) -> None:
        # Connections are transaction-scoped; no global connection to leak across forks.
        self.owner = None

    def _job(self, db, job_id: str):
        _valid_id(job_id, "job")
        row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, self.owner)).fetchone()
        if row is None:
            raise fail("not_found", "job_read")
        return row

    def _event(self, db, job_id: str, kind: str, data: dict) -> int:
        payload = _json(data)
        if len(payload.encode()) > MAX_EVENT_BYTES or not re.fullmatch(r"[a-z_]{1,40}", kind):
            raise fail("invalid_argument", "job_event")
        return db.execute("INSERT INTO events(job_id,observed_at,kind,data) VALUES (?,?,?,?)",
                          (job_id, utc_now(), kind, payload)).lastrowid

    def create_job(self, operation: str, *, idempotency_key: str | None = None,
                   request: dict | None = None, total_steps: int | None = None) -> Job:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", operation):
            raise fail("invalid_argument", "job_create")
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise fail("invalid_argument", "job_create")
        request_hash = hashlib.sha256(_json(request or {}).encode()).hexdigest()
        with self._db(write=True) as db:
            if idempotency_key is not None:
                old = db.execute("SELECT request_hash,document FROM jobs WHERE owner=? AND operation=? AND idem=?",
                                 (self.owner, operation, idempotency_key)).fetchone()
                if old:
                    if old["request_hash"] != request_hash:
                        raise fail("invalid_argument", "job_create")
                    return Job.model_validate_json(old["document"])
            job = Job(job_id=new_id("job"), attempt_id=new_id("attempt"), queued_at=utc_now(), total_steps=total_steps)
            db.execute("INSERT INTO jobs(id,owner,operation,idem,request_hash,document) VALUES (?,?,?,?,?,?)",
                       (job.job_id, self.owner, operation, idempotency_key, request_hash, job.model_dump_json()))
            self._event(db, job.job_id, "created", {"state": job.state, "operation": operation})
            return job

    def get_job(self, job_id: str) -> Job:
        with self._db() as db:
            return Job.model_validate_json(self._job(db, job_id)["document"])

    def list_jobs(self, *, cursor: int = 0, limit: int = 25, active_only: bool = False) -> JobPage:
        _page(limit, cursor)
        with self._db() as db:
            clause = " AND json_extract(document, '$.state') NOT IN ('completed','failed','cancelled','interrupted')" if active_only else ""
            rows = db.execute("SELECT seq,document FROM jobs WHERE owner=? AND seq>?" + clause + " ORDER BY seq LIMIT ?",
                              (self.owner, cursor, limit + 1)).fetchall()
            selected = rows[:limit]
            return JobPage(jobs=[Job.model_validate_json(r["document"]) for r in selected],
                           next_cursor=selected[-1]["seq"] if len(rows) > limit else None, observed_at=utc_now())

    def update_job(self, job_id: str, *, state: State | None = None, **changes) -> Job:
        allowed = {"phase", "started_at", "completed_steps", "total_steps", "progress_text", "run_ids", "artifact_uris", "error"}
        if changes.keys() - allowed:
            raise fail("invalid_argument", "job_update")
        with self._db(write=True) as db:
            old = Job.model_validate_json(self._job(db, job_id)["document"])
            if old.state in TERMINAL or state in TERMINAL:
                raise fail("refused_by_policy", "job_update")
            if state is not None and state != old.state and state not in TRANSITIONS.get(old.state, set()):
                raise fail("invalid_argument", "job_update")
            data = old.model_dump()
            data.update(changes)
            if state is not None:
                data["state"] = state
            job = Job.model_validate(data)
            if job.completed_steps < old.completed_steps or (job.total_steps is not None and job.completed_steps > job.total_steps):
                raise fail("invalid_argument", "job_update")
            db.execute("UPDATE jobs SET document=? WHERE id=?", (job.model_dump_json(), job_id))
            self._event(db, job_id, "progress", {"state": job.state, "phase": job.phase,
                        "completed_steps": job.completed_steps, "total_steps": job.total_steps,
                        "progress_text": job.progress_text})
            return job

    def append_event(self, job_id: str, kind: str, data: dict) -> int:
        with self._db(write=True) as db:
            self._job(db, job_id)
            return self._event(db, job_id, kind, data)

    def latest_event(self, job_id: str, kind: str) -> Event | None:
        with self._db() as db:
            self._job(db, job_id)
            row = db.execute("SELECT * FROM events WHERE job_id=? AND kind=? ORDER BY seq DESC LIMIT 1",
                             (job_id, kind)).fetchone()
            return Event(cursor=row["seq"], job_id=job_id, observed_at=row["observed_at"],
                         kind=row["kind"], data=json.loads(row["data"])) if row else None

    def events(self, job_id: str, *, cursor: int = 0, limit: int = 50) -> EventPage:
        _page(limit, cursor)
        with self._db() as db:
            self._job(db, job_id)
            rows = db.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (job_id, cursor, limit + 1)).fetchall()
            selected = rows[:limit]
            return EventPage(events=[Event(cursor=r["seq"], job_id=job_id, observed_at=r["observed_at"],
                kind=r["kind"], data=json.loads(r["data"])) for r in selected],
                next_cursor=selected[-1]["seq"] if selected else cursor, has_more=len(rows) > limit)

    def bind_process(self, job_id: str, process: ProcessIdentity, supervisor: ProcessIdentity) -> None:
        with self._db(write=True) as db:
            row = self._job(db, job_id)
            if row["process"] is not None or Job.model_validate_json(row["document"]).state in TERMINAL:
                raise fail("refused_by_policy", "job_start")
            db.execute("UPDATE jobs SET process=?,supervisor=? WHERE id=?", (process.model_dump_json(), supervisor.model_dump_json(), job_id))
            self._event(db, job_id, "process_started", {"pid": process.pid, "start_ticks": process.start_ticks})

    def bind_operation(self, job_id: str, owner: ProcessIdentity) -> None:
        with self._db(write=True) as db:
            row = self._job(db, job_id)
            if row["process"] is not None or Job.model_validate_json(row["document"]).state != State.QUEUED:
                raise fail("refused_by_policy", "job_start")
            db.execute("UPDATE jobs SET process=?,supervisor=? WHERE id=?",
                       (owner.model_dump_json(), owner.model_dump_json(), job_id))
            self._event(db, job_id, "operation_started", {"pid": owner.pid, "start_ticks": owner.start_ticks})

    def process(self, job_id: str) -> tuple[ProcessIdentity | None, ProcessIdentity | None]:
        with self._db() as db:
            row = self._job(db, job_id)
            return tuple(ProcessIdentity.model_validate_json(row[key]) if row[key] else None for key in ("process", "supervisor"))

    def claim_authorization(self, authorization_id: str, plan_hash: str, job_id: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", authorization_id) or not re.fullmatch(r"[0-9a-f]{64}", plan_hash):
            raise fail("invalid_argument", "authorization_claim")
        key = "authorization:" + authorization_id
        value = _json({"plan_hash": plan_hash, "job_id": job_id})
        with self._db(write=True) as db:
            self._job(db, job_id)
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if row is not None and row[0] != value:
                raise fail("refused_by_policy", "authorization_claim")
            db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, value))

    def request_cancel(self, job_id: str) -> Job:
        with self._db(write=True) as db:
            row = self._job(db, job_id)
            job = Job.model_validate_json(row["document"])
            if job.state not in TERMINAL and not row["cancel_requested"]:
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
                self._event(db, job_id, "cancel_requested", {})
            return job

    def cancellation_requested(self, job_id: str) -> bool:
        with self._db() as db:
            return bool(self._job(db, job_id)["cancel_requested"])

    def own_run(self, job_id: str, run_id: str) -> None:
        """Opt into receipt propagation; ordinary run_ids are only references."""
        _valid_id(run_id, "run")
        with self._db(write=True) as db:
            job = Job.model_validate_json(self._job(db, job_id)["document"])
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise fail("not_found", "run_ownership")
            run = Run.model_validate_json(row[0])
            if job.state in TERMINAL or run.state in TERMINAL:
                raise fail("refused_by_policy", "run_ownership")
            owner = db.execute("SELECT job_id FROM run_owners WHERE run_id=?", (run_id,)).fetchone()
            if owner is not None and owner[0] != job_id:
                raise fail("refused_by_policy", "run_ownership")
            db.execute("INSERT OR IGNORE INTO run_owners VALUES (?,?)", (run_id, job_id))
            if run_id not in job.run_ids:
                data = job.model_dump()
                data["run_ids"].append(run_id)
                updated = Job.model_validate(data)
                db.execute("UPDATE jobs SET document=? WHERE id=?", (updated.model_dump_json(), job_id))
            if owner is None:
                self._event(db, job_id, "run_owned", {"run_id": run_id})

    def finish(self, job_id: str, state: State, receipt: Receipt, *, error=None) -> Job:
        if state not in TERMINAL or receipt.job_id != job_id:
            raise fail("invalid_argument", "job_finish")
        with self._db(write=True) as db:
            row = self._job(db, job_id)
            old = Job.model_validate_json(row["document"])
            if old.state in TERMINAL or row["receipt"]:
                if row["receipt"] == receipt.model_dump_json() and old.state == state:
                    return old
                raise fail("refused_by_policy", "job_finish")
            data = old.model_dump()
            data.update(state=state, phase="finished", error=error)
            job = Job.model_validate(data)
            db.execute("UPDATE jobs SET document=?,receipt=? WHERE id=?", (job.model_dump_json(), receipt.model_dump_json(), job_id))
            self._event(db, job_id, "finished", receipt.model_dump())
            owned = db.execute("SELECT run_id FROM run_owners WHERE job_id=?", (job_id,)).fetchall()
            for (run_id,) in owned:
                row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise fail("not_found", "job_finish")
                run = Run.model_validate_json(row[0])
                if run.state in TERMINAL:
                    continue
                data = run.model_dump(by_alias=True)
                data.update(state=state, ended_at=receipt.observed_at, exit=receipt.exit_code,
                            stop_reason=receipt.stop_reason)
                for clock in ("queue_seconds", "held_seconds"):
                    if getattr(receipt, clock) is not None:
                        data["gate"][clock] = getattr(receipt, clock)
                completed_run = Run.model_validate(data)
                db.execute("UPDATE runs SET document=? WHERE id=?",
                           (completed_run.model_dump_json(by_alias=True), run_id))
            return job

    def receipt(self, job_id: str) -> Receipt | None:
        with self._db() as db:
            raw = self._job(db, job_id)["receipt"]
            return Receipt.model_validate_json(raw) if raw else None

    def create_run(self, **fields) -> Run:
        if {"run_id", "attempt_id", "owner"} & fields.keys():
            raise fail("invalid_argument", "run_create")
        run = Run(run_id=new_id("run"), attempt_id=new_id("attempt"), owner=self.owner, **fields)
        with self._db(write=True) as db:
            db.execute("INSERT INTO runs VALUES (?,?)", (run.run_id, run.model_dump_json(by_alias=True)))
        return run

    def get_run(self, run_id: str) -> Run:
        _valid_id(run_id, "run")
        with self._db() as db:
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise fail("not_found", "run_read")
            return Run.model_validate_json(row[0])

    def update_run(self, run_id: str, **changes) -> Run:
        if {"run_id", "attempt_id", "owner"} & changes.keys():
            raise fail("invalid_argument", "run_update")
        _valid_id(run_id, "run")
        with self._db(write=True) as db:
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise fail("not_found", "run_update")
            old = Run.model_validate_json(row[0])
            state = changes.get("state", old.state)
            if old.state in TERMINAL or (state != old.state and state not in TRANSITIONS.get(old.state, set())):
                raise fail("refused_by_policy", "run_update")
            data = old.model_dump(by_alias=True)
            data.update(changes)
            run = Run.model_validate(data)
            db.execute("UPDATE runs SET document=? WHERE id=?", (run.model_dump_json(by_alias=True), run_id))
            return run

    def add_artifact(self, path: Path, *, guards, mime: str, producer: Producer,
                     run_id: str | None = None, clock: str | None = None,
                     privacy: str = "private", complete: bool = False) -> Artifact:
        path = guards.read(path)
        if run_id:
            self.get_run(run_id)
        with path.open("rb") as stream:
            before = path.stat()
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise fail("busy", "artifact_register", retryable=True)
        ident = new_id("artifact")
        artifact = Artifact(id=ident, uri="workbench://artifacts/" + ident, mime=mime,
            bytes=after.st_size, sha256=digest, producer=producer, run_id=run_id,
            clock=clock, privacy=privacy, complete=complete)
        with self._db(write=True) as db:
            db.execute("INSERT INTO artifacts VALUES (?,?,?)", (ident, artifact.model_dump_json(), str(path)))
        return artifact

    def get_artifact(self, artifact_id: str) -> Artifact:
        _valid_id(artifact_id, "artifact")
        with self._db() as db:
            row = db.execute("SELECT document FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise fail("not_found", "artifact_read")
            return Artifact.model_validate_json(row[0])

    def artifact_path(self, artifact_id: str, guards) -> Path:
        _valid_id(artifact_id, "artifact")
        with self._db() as db:
            row = db.execute("SELECT path FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise fail("not_found", "artifact_read")
            return guards.read(Path(row[0]))
