"""One lifecycle-owned dependency object shared by registered groups."""
from dataclasses import dataclass, field
from src.config import Config
from src.errors import fail
from src.effects.gates import GateReader
from src.policy import PathGuards, Policy, ToolSpec, validate_state_root
from src.registry import Registry
from src.registry.supervisor import Supervisor


@dataclass
class Deps:
    config: Config
    specs: dict[str, ToolSpec] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)
    selected: frozenset[str] = frozenset()
    gates: object | None = None
    services: dict[str, object] = field(default_factory=dict)
    shutdown_reason: str = "server_shutdown"
    reconcile_on_open: bool = True

    def __post_init__(self):
        validate_state_root(self.config)
        self.policy = Policy(self.config)
        self.paths = PathGuards(self.config)
        self.registry = Registry(self.config.state_root)
        self.supervisor = Supervisor(self.registry)
        if self.gates is None:
            self.gates = GateReader(self.config)
        self._cleanups = []
        self._pre_cleanups = []

    error = staticmethod(fail)

    async def open(self):
        self.registry.open()
        if not self.reconcile_on_open:
            return
        cursor = 0
        while True:
            page = self.registry.list_jobs(cursor=cursor, limit=100, active_only=True)
            for job in page.jobs:
                self.supervisor.reconcile(job.job_id)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

    def add_cleanup(self, cleanup, *, before_workers=False):
        (self._pre_cleanups if before_workers else self._cleanups).append(cleanup)

    async def close(self):
        try:
            for cleanup in reversed(self._pre_cleanups):
                await cleanup()
        finally:
            try:
                await self.supervisor.close()
            finally:
                try:
                    for cleanup in reversed(self._cleanups):
                        await cleanup()
                finally:
                    self.registry.close()

    def tool(self, mcp, name: str):
        spec = self.specs[name]
        if name not in self.selected:
            return lambda fn: fn
        return mcp.tool(name=name, description=spec.description,
            annotations=spec.annotations, tags={spec.safety.value})

    def tool_catalogue(self) -> list[dict]:
        return [{"name": name, "group": self.groups[name], "safety": self.specs[name].safety.value,
                 "annotations": self.specs[name].annotations, "requires_broker": self.specs[name].requires_broker,
                 "shell_route": self.specs[name].shell_route} for name in sorted(self.selected)]
