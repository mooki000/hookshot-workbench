"""Optional proposed-file observations; independent of timing evidence dispatch."""
from src.effects.system_telemetry import observe, TelemetryObservation
from src.models.evidence import RunId, FrameWindow, TimeWindow
from src.policy import Safety, ToolSpec

SPECS = (
    ToolSpec("system_telemetry", Safety.READ,
        "Read registered perf.csv only when present. Its writer/format is still proposed in Hookshot issue #2915; no speed verdict.",
        shell_route="Optional proposed perf.csv reader (#2915); no file means unavailable observation, not an error"),
)


def register(mcp, deps):
    @deps.tool(mcp, "system_telemetry")
    async def system_telemetry(run_id: RunId, frame_window: FrameWindow | None = None,
                               time_window: TimeWindow | None = None) -> TelemetryObservation:
        return await observe(deps, run_id, frame_window, time_window)
