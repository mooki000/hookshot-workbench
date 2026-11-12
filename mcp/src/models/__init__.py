"""Stable wire records; absent evidence remains null."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(max_length=2048)]
Identifier = Annotated[str, Field(pattern=r"^[a-z]+_[0-9a-f]{32}$")]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class State(StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL = frozenset({State.COMPLETED, State.FAILED, State.CANCELLED, State.INTERRUPTED})


class Build(Record):
    commit: str | None = None
    source_stamp: str | None = None
    binary_hashes: dict[str, str] = Field(default_factory=dict)
    profile_id: str | None = None


class Gate(Record):
    request_id: str | None = None
    class_: str | None = Field(default=None, alias="class", serialization_alias="class")
    device: str | None = None
    queue_seconds: float | None = None
    held_seconds: float | None = None


class Run(Record):
    run_id: Identifier
    attempt_id: Identifier
    owner: str
    state: State = State.PLANNED
    build: Build = Field(default_factory=Build)
    config_hash: str | None = None
    policy_revision: str | None = None
    gate: Gate = Field(default_factory=Gate)
    capabilities: dict[str, bool | str | None] = Field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    exit: int | None = None
    stop_reason: Text | None = None
    timing_valid: bool | None = None
    artifacts: list[str] = Field(default_factory=list, max_length=1000)
    warnings: list[Text] = Field(default_factory=list, max_length=100)


class ErrorEnvelope(Record):
    code: Literal["not_found", "invalid_argument", "refused_by_policy", "unavailable", "busy", "timeout", "uncertain_outcome", "backend_failed"]
    operation: str
    retryable: bool
    retry_after_seconds: float | None = None
    operator_action: str


class Job(Record):
    job_id: Identifier
    attempt_id: Identifier
    state: State = State.QUEUED
    phase: str = "queued"
    queued_at: str
    started_at: str | None = None
    completed_steps: int = Field(default=0, ge=0)
    total_steps: int | None = Field(default=None, ge=0)
    progress_text: Text = ""
    run_ids: list[str] = Field(default_factory=list, max_length=1000)
    artifact_uris: list[str] = Field(default_factory=list, max_length=1000)
    error: ErrorEnvelope | None = None


class Producer(Record):
    tool: str
    version: str | None = None
    source_commit: str | None = None


class Artifact(Record):
    id: Identifier
    uri: str
    mime: str
    bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    producer: Producer
    run_id: str | None = None
    clock: str | None = None
    privacy: Literal["private", "public_candidate"] = "private"
    complete: bool = False


class ProcessIdentity(Record):
    pid: int
    start_ticks: int
    boot_id: str
    process_group: int
    session_id: int


class Receipt(Record):
    job_id: str
    observed_at: str
    exit_code: int | None
    stop_reason: str
    process_exited: bool
    descendants_exited: bool | None
    queue_seconds: float | None
    held_seconds: float | None


class Event(Record):
    cursor: int
    job_id: str
    observed_at: str
    kind: str
    data: dict


class EventPage(Record):
    events: list[Event]
    next_cursor: int
    has_more: bool


class JobPage(Record):
    jobs: list[Job]
    next_cursor: int | None
    observed_at: str


class RunPage(Record):
    runs: list[Run]
    next_cursor: int | None
    observed_at: str


class JobView(Record):
    job: Job
    events: EventPage
    receipt: Receipt | None
    observed_at: str


class WaitResult(Record):
    job: Job
    terminal: bool
    timed_out: bool
    receipt: Receipt | None
    observed_at: str
