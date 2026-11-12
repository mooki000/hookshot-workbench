"""Session wire records and operator-only catalogue. No caller-selected paths or commands.

Input ``hos`` units follow the bridge's documented 60 game presents/second:
``at`` is seconds, ``duration`` milliseconds, rounded half away from zero;
positive holds shorter than a frame become one frame. This is not wall time.
"""
import math
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field, model_validator
from src.models import Artifact, Build, Record

RunId = Annotated[str, Field(pattern=r"^run_[0-9a-f]{32}$")]
LocalId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Label = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")]
Limit = Annotated[int, Field(ge=1, le=100)]
Cursor = Annotated[int, Field(ge=0, le=10000)]
Timeout = Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)]
Idempotency = Annotated[str, Field(min_length=1, max_length=128)]
Button = Literal["A", "B", "X", "Y", "L", "R", "ZL", "ZR", "PLUS", "MINUS", "LS", "RS", "LEFT", "UP", "RIGHT", "DOWN"]


class InputStep(Record):
    kind: Literal["press", "stick", "release"]
    buttons: list[Button] = Field(default_factory=list, max_length=16)
    stick: Literal["L", "R"] = "L"
    x: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    y: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    at: float = Field(default=0, ge=0, le=216000, allow_inf_nan=False)
    duration: float = Field(default=8, gt=0, le=3600000, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent(self):
        if self.kind == "press" and (not self.buttons or self.x or self.y):
            raise ValueError("press requires buttons and no axes")
        if self.kind == "release" and (self.buttons or self.x or self.y or self.at):
            raise ValueError("release takes no input")
        return self

    def bridge_step(self, units: str) -> dict:
        if units == "frames":
            if not float(self.at).is_integer() or not float(self.duration).is_integer():
                raise ValueError("frame units must be integral")
            at, frames = int(self.at), int(self.duration)
        else:
            at = math.floor(self.at * 60 + 0.5)
            frames = max(1, math.floor(self.duration * 60 / 1000 + 0.5))
        if at > 216000 or frames > 216000 or at + frames > 216000:
            raise ValueError("sequence is too long")
        value = {"at": at, "frames": frames}
        if self.buttons:
            value["buttons"] = self.buttons
        if self.kind == "stick":
            value.update(stick=self.stick, x=self.x, y=self.y)
        return value


class Predicate(Record):
    kind: Literal["frames", "frame", "state", "map", "controllable", "field"]
    frames: int | None = Field(default=None, ge=1, le=216000)
    frame: int | None = Field(default=None, ge=0)
    state: Literal["boot", "intro", "menu", "loading", "play", "unknown"] | None = None
    map: str | None = Field(default=None, min_length=1, max_length=80)
    field: Literal["event", "loading", "age"] | None = None
    value: bool | str | None = Field(default=None)

    @model_validator(mode="after")
    def complete(self):
        required = {"frames": self.frames, "frame": self.frame, "state": self.state, "map": self.map,
                    "field": self.field, "controllable": True}[self.kind]
        if required is None or (self.kind == "field" and self.value is None):
            raise ValueError("predicate is missing its value")
        if isinstance(self.value, str) and len(self.value) > 80:
            raise ValueError("value is too long")
        return self


class GameSample(Record):
    map: str | None = Field(default=None, max_length=80)
    event: bool | None = None
    loading: bool | None = None
    age: str | None = Field(default=None, max_length=32)


class Sample(Record):
    protocol: int | None = None
    capabilities: dict[str, bool | None]
    observed_at: str
    frame: int | None = None
    sample_frame: int | None = None
    sample_time: float | None = None
    age_ms: float | None = None
    fresh: bool | None = None
    state: str | None = Field(default=None, max_length=16)
    game: GameSample
    controllable: bool | None = None
    unknown_fields: list[str]


class InputResult(Record):
    action_id: str
    sent: bool | None
    queued: int | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    frame: int | None = None
    completed: bool | None
    effect_unverified: Literal[True] = True


class PredicateResult(Record):
    matched: bool | None
    sample: Sample
    frames_waited: int | None
    elapsed_ms: float
    reason: Literal["matched", "timeout", "freshness_unsupported", "cancelled"]


class ShotResult(Record):
    artifact: Artifact
    present: int | None
    frame: int | None
    width: int | None
    height: int | None
    overlay: Literal[False] = False


class Option(Record):
    key: Key
    value: int | float | bool | None = None
    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    restart: bool | None = None
    fixed: bool | None = None
    text: str | None = Field(default=None, max_length=160)


class OptionsPage(Record):
    options: list[Option]
    next_cursor: int | None
    observed_at: str


class OptionsChanged(Record):
    before: list[Option]
    after: list[Option]
    effective: bool | None
    restart_required: bool | None
    restoration: Literal["not_requested", "on_finish"]


class TravelResult(Record):
    sent: bool | None
    accepted: bool | None
    arrived: bool | None
    controllable: bool | None
    state: Sample


class SaveFile(Record):
    name: str = Field(max_length=80)
    size: int | None = Field(default=None, ge=0)
    mtime: float | None = None


class SaveSlot(Record):
    slot: str = Field(pattern=r"^[0-9]{1,3}$")
    mtime: float | None = None
    newest: bool | None = None
    files: list[SaveFile] = Field(default_factory=list, max_length=32)


class SavesPage(Record):
    slots: list[SaveSlot]
    next_cursor: int | None
    observed_at: str
    write_consistency: Literal["not_guaranteed"] = "not_guaranteed"


class StopResult(Record):
    method: Literal["already_exited", "quit", "terminate", "cancelled_before_start"]
    process_exited: bool
    save_drain: Literal["unsupported"] = "unsupported"
    forced: bool
    artifacts_complete: bool | None


class BuildEntry(Record):
    path: Path
    reviewed: Literal[True]
    owned: Literal[True]
    provenance: Build = Field(default_factory=Build)


class SaveEntry(Record):
    path: Path
    provenance: str = Field(default="operator_registered", max_length=160)


class SessionProfile(Record):
    lane: Literal["gpu", "intel"] = "gpu"
    resource_class: Literal["ordinary", "whole"] = "ordinary"
    disposable: Literal[True] = True
    debug: bool = False
    max_wall_seconds: int = Field(default=450, ge=1, le=450)

    @model_validator(mode="after")
    def lane_class(self):
        if self.lane == "intel" and self.resource_class != "ordinary":
            raise ValueError("Intel does not have whole-card mode")
        return self


class SessionCatalogue(Record):
    builds: dict[LocalId, BuildEntry] = Field(default_factory=dict, max_length=1000)
    saves: dict[LocalId, SaveEntry] = Field(default_factory=dict, max_length=1000)
    profiles: dict[LocalId, SessionProfile] = Field(default_factory=dict, max_length=100)
    clone_root: Path
