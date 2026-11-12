"""Explicit environment configuration. No dotenv or credential discovery."""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from src.models.knowledge import KnowledgeConfig

PREFIX = "HOOKSHOT_WB_"


def _absolute(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Configured paths must be absolute")
    return path.resolve()


@dataclass(frozen=True)
class Config:
    state_root: Path
    harness_dir: Path | None = None
    gpu_gate: Path | None = None
    intel_gate: Path | None = None
    build_gate: Path | None = None
    idle_gate: Path | None = None
    panels_script: Path | None = None
    journal_reader: Path | None = None
    session_catalogue: Path | None = None
    checks_catalogue: Path | None = None
    knowledge: KnowledgeConfig | None = None
    knowledge_unavailable_reason: str | None = None
    checkout: Path | None = None
    dependency_python: Path = Path(sys.executable)
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] = ()
    protected_roots: tuple[Path, ...] = ()
    profile: str = "observe"
    extra_tools: tuple[str, ...] = ()
    broker_idle_seconds: float = 60

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Config":
        e = os.environ if environ is None else environ
        def path(name: str) -> Path | None:
            value = e.get(PREFIX + name)
            return _absolute(value) if value else None
        def roots(name: str) -> tuple[Path, ...]:
            try:
                values = json.loads(e.get(PREFIX + name, "[]"))
                if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                    raise ValueError
                return tuple(_absolute(v) for v in values)
            except (ValueError, TypeError):
                raise ValueError(f"{PREFIX}{name} must be a JSON array of absolute paths") from None
        home = Path(e.get("HOME", str(Path.home()))).expanduser().resolve()
        state = path("STATE_ROOT") or _absolute(e.get("XDG_STATE_HOME", str(home / ".local/state"))) / "hookshot-workbench"
        # Secrets and ordinary download folders can never be registered as data roots.
        protected = (home / ".config", home / ".ssh", home / "Downloads", *roots("PROTECTED_ROOTS"))
        try:
            idle = float(e.get(PREFIX + "BROKER_IDLE_SECONDS", "60"))
            if not 0.05 <= idle <= 3600:
                raise ValueError
        except ValueError:
            raise ValueError("HOOKSHOT_WB_BROKER_IDLE_SECONDS must be between 0.05 and 3600") from None
        knowledge = None
        knowledge_reason = "not_configured"
        if knowledge_path := path("KNOWLEDGE_CONFIG"):
            try:
                if any(knowledge_path.is_relative_to(p.resolve()) for p in protected):
                    raise ValueError
                knowledge = KnowledgeConfig.from_file(knowledge_path)
                knowledge_reason = None
            except ValueError:
                knowledge_reason = "invalid_or_unreadable_operator_configuration"
        return cls(state_root=state, harness_dir=path("HARNESS_DIR"),
            gpu_gate=path("GPU_GATE"), intel_gate=path("INTEL_GATE"), build_gate=path("BUILD_GATE"),
            idle_gate=path("IDLE_GATE"), panels_script=path("PANELS_SCRIPT"), journal_reader=path("JOURNAL_READER"),
            session_catalogue=path("SESSION_CATALOGUE"), checks_catalogue=path("CHECKS_CATALOGUE"),
            knowledge=knowledge, knowledge_unavailable_reason=knowledge_reason,
            broker_idle_seconds=idle,
            checkout=path("CHECKOUT"), dependency_python=path("DEPENDENCY_PYTHON") or Path(sys.executable),
            read_roots=roots("READ_ROOTS"), write_roots=roots("WRITE_ROOTS"),
            protected_roots=protected, profile=e.get(PREFIX + "PROFILE", "observe"),
            extra_tools=tuple(x.strip() for x in e.get(PREFIX + "EXTRA_TOOLS", "").split(",") if x.strip()))
