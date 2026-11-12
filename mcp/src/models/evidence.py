"""Bounded evidence requests and results; no caller-selected executable or file path."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from src.models import Artifact, Producer, Record

RunId = Annotated[str, Field(pattern=r"^run_[0-9a-f]{32}$")]
ArtifactId = Annotated[str, Field(pattern=r"^artifact_[0-9a-f]{32}$")]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]
Key = Annotated[str, Field(min_length=1, max_length=128)]
Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")]
Caption = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[^\x00-\x1f@%]+$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Signature = Literal["fatal", "device_lost", "memory", "readback", "driver", "warnings"]
AnalyzerId = Literal["present_frame_times", "readback_waits", "thread_cpu", "memory_lines"]


class FrameWindow(Record):
    first: int = Field(ge=0, le=2**32 - 1)
    last: int = Field(ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def ordered(self):
        if self.last <= self.first:
            raise ValueError("Window must advance")
        return self


class TimeWindow(Record):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Window must advance")
        return self


class AnalysisArgs(Record):
    input_artifact_id: ArtifactId
    frame_window: FrameWindow | None = None
    time_window: TimeWindow | None = None
    samples_artifact_id: ArtifactId | None = None


class LogPage(Record):
    lines: list[str] = Field(max_length=500)
    next_cursor: str | None
    truncated: bool
    complete: bool
    redactions: int = 0
    omitted_lines: int = 0


class AnalysisResult(Record):
    run_id: RunId
    analyzer_id: AnalyzerId
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    clock: str | None
    window: dict | None
    validity: Literal["valid", "rejected"]
    rejections: list[str] = Field(default_factory=list, max_length=30)
    producer: Producer
    inputs: list[ArtifactId] = Field(max_length=3)
    source: str
    context: dict
    artifacts: list[Artifact] = Field(default_factory=list, max_length=4)


class PanelImage(Record):
    artifact_id: ArtifactId
    caption: Caption


class PanelLayout(Record):
    per_row: int = Field(default=2, ge=1, le=4)
    width: int = Field(default=640, ge=64, le=1280)
    height: int = Field(default=360, ge=64, le=720)
    crop: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,3}x[1-9][0-9]{0,3}\+[0-9]{1,4}\+[0-9]{1,4}$")] | None = None


class MetricRun(Record):
    run_id: RunId
    metrics_artifact_id: ArtifactId


class RunPair(Record):
    reference: MetricRun
    candidate: MetricRun


class Pairing(Record):
    pairs: list[RunPair] = Field(min_length=2, max_length=2)
    metric: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]
    direction: Literal["higher-worse", "lower-worse"] = "higher-worse"


class SelectedPicture(Record):
    artifact_id: ArtifactId
    inspection: Literal["pending", "inspected"] = "pending"
    inspection_note: Annotated[str, Field(max_length=500)] | None = None

    @model_validator(mode="after")
    def attested(self):
        if self.inspection == "inspected" and not self.inspection_note:
            raise ValueError("Inspection needs an explicit observation")
        return self


class BundleRequest(Record):
    base: Sha
    head: Sha
    topic: Label | None = None
    pull_request: int | None = Field(default=None, ge=1)
    rule: Literal["source", "runtime", "performance", "visual"] = "runtime"
    run_ids: list[RunId] = Field(default_factory=list, max_length=16)
    build_artifact_ids: list[ArtifactId] = Field(default_factory=list, max_length=8)
    check_ids: list[JobId] = Field(default_factory=list, max_length=16)
    metrics_artifact_ids: list[ArtifactId] = Field(default_factory=list, max_length=32)
    pictures: list[SelectedPicture] = Field(default_factory=list, max_length=16)
    pairing: Pairing | None = None
    limitations: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def target(self):
        if self.topic is None and self.pull_request is None:
            raise ValueError("A topic or pull request is required")
        return self
