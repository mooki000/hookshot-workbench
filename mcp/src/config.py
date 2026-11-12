"""Explicit environment configuration. No dotenv or credential discovery."""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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
    checkout: Path | None = None
    dependency_python: Path = Path(sys.executable)
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] = ()
    protected_roots: tuple[Path, ...] = ()
    profile: str = "observe"
    extra_tools: tuple[str, ...] = ()

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
        return cls(state_root=state, harness_dir=path("HARNESS_DIR"),
            gpu_gate=path("GPU_GATE"), intel_gate=path("INTEL_GATE"), build_gate=path("BUILD_GATE"),
            checkout=path("CHECKOUT"), dependency_python=path("DEPENDENCY_PYTHON") or Path(sys.executable),
            read_roots=roots("READ_ROOTS"), write_roots=roots("WRITE_ROOTS"),
            protected_roots=protected, profile=e.get(PREFIX + "PROFILE", "observe"),
            extra_tools=tuple(x.strip() for x in e.get(PREFIX + "EXTRA_TOOLS", "").split(",") if x.strip()))
