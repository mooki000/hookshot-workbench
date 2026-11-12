"""Only fixed, actionable messages cross the protocol boundary."""
import re
from fastmcp.exceptions import ToolError
from src.models import ErrorEnvelope

ACTIONS = {
    "not_found": "Use an identifier returned by this server.",
    "invalid_argument": "Correct the arguments using the advertised schema.",
    "refused_by_policy": "Ask the operator to review the scoped policy outside MCP.",
    "unavailable": "Configure the required local component outside MCP.",
    "busy": "Wait for the existing operation; do not submit a duplicate.",
    "timeout": "Inspect observed state before deciding whether to wait again.",
    "uncertain_outcome": "Reconcile the existing attempt; do not replay the mutation.",
    "backend_failed": "Inspect local operator diagnostics; do not replay a mutation.",
}


class WorkbenchError(ToolError):
    def __init__(self, code: str, operation: str, *, retryable: bool = False,
                 retry_after_seconds: float | None = None):
        if code not in ACTIONS:
            code = "backend_failed"
        operation = operation if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", operation) else "request"
        self.envelope = ErrorEnvelope(code=code, operation=operation,
            retryable=retryable, retry_after_seconds=retry_after_seconds,
            operator_action=ACTIONS[code])
        super().__init__(self.envelope.model_dump_json())


def fail(code: str, operation: str, *, retryable: bool = False) -> WorkbenchError:
    return WorkbenchError(code, operation, retryable=retryable)
