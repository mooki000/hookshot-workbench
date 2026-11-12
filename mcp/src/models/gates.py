"""Read-only, time-stamped gate observations, not scheduling promises."""
from typing import Literal
from pydantic import Field
from src.models import Record


class Estimate(Record):
    observed_at: str
    range_seconds: tuple[float, float] | None = None
    basis: str = "Holder durations and future admission conditions are unknown."
    confidence: Literal["unknown", "conditional"] = "unknown"


class Holder(Record):
    observed_at: str
    slot: str
    pid: int | None
    process_start_ticks: int | None
    process_alive: bool | None
    resource_class: str | None
    age_seconds: float | None
    age_basis: str
    lock_mode: str


class Waiter(Record):
    observed_at: str
    ticket: str | None
    pid: int
    process_start_ticks: int | None
    priority_class: Literal["ordinary", "priority", "first", "unknown"]
    resource_class: str | None
    age_seconds: float | None
    age_basis: str
    yielded: bool = False
    position_or_null: int | None = None


class Quiet(Record):
    observed_at: str
    known: bool
    holders: list[Holder] = Field(default_factory=list)
    wanted_pids: list[int] = Field(default_factory=list)
    stale_markers: int = 0
    exclusive: bool | None = None


class DeviceQueue(Record):
    observed_at: str
    device: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    source_sha256: str | None = None
    holders: list[Holder] = Field(default_factory=list)
    waiters: list[Waiter] = Field(default_factory=list)
    yielded: list[Waiter] = Field(default_factory=list)
    stale_tickets: int = 0
    stale_holder_files: int = 0
    position_or_null: int | None = None
    position_basis: str
    blocking_reasons: list[str] = Field(default_factory=list)
    eta: Estimate
    complete: bool = True
    next_cursor: int | None = None


class QueueSnapshot(Record):
    observed_at: str
    devices: list[DeviceQueue]
    quiet: Quiet
    atomic: bool = False
    note: str = "A read-only snapshot; state can change between procfs and file reads. No slot is reserved."
