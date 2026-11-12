"""Status result schemas distinguish configuration from observed connectivity."""
from typing import Literal
from src.models import Identifier, JobPage, Receipt, Record
from src.models.gates import QueueSnapshot
from src.policy import PolicySnapshot


class Capability(Record):
    status: Literal["configured", "unavailable"]
    reason: str | None
    connectivity: bool | None = None


class ToolInfo(Record):
    name: str
    group: str
    safety: str
    annotations: dict[str, bool]
    requires_broker: bool
    shell_route: str


class WorkbenchStatus(Record):
    observed_at: str
    policy: PolicySnapshot
    capabilities: dict[str, Capability]
    active_jobs: JobPage
    protected_player_active: bool | None
    gate_summary: QueueSnapshot
    tool_revisions: dict[str, str | None]
    tools: list[ToolInfo]


class CancelResult(Record):
    cancel_requested: bool
    job_id: Identifier
    receipt: Receipt | None
