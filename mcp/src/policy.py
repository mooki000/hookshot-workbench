"""Enforced local boundaries; MCP annotations are merely descriptive hints."""
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal
from pydantic import Field, ValidationError
from src.config import Config
from src.errors import fail
from src.models import Record, utc_now


class Safety(StrEnum):
    READ = "READ"
    RUN = "RUN"
    WRITE_LOCAL = "WRITE_LOCAL"
    PUBLIC = "PUBLIC"
    DANGER = "DANGER"


DENIED_TOOLS = frozenset({
    "fork_ci_start", "git_push", "post_comment", "request_claim", "request_renew", "request_done",
    "team_post", "login", "logout", "token_read", "gate_reopen", "gate_bypass", "set_gpu_slots",
    "window_enable", "window_open", "shell", "python_eval", "raw_bridge", "memory_write",
    "delete_run", "install_to_player", "save_import_export", "rebase_force", "compositor_command", "timer_arm",
})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    safety: Safety
    description: str
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False
    broker: bool = False
    shell_route: str = ""

    @property
    def requires_broker(self) -> bool:
        return self.safety != Safety.READ or self.broker

    @property
    def annotations(self) -> dict[str, bool]:
        return {"readOnlyHint": self.safety == Safety.READ, "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent, "openWorldHint": self.open_world}


def check_spec(spec: ToolSpec) -> None:
    if spec.name in DENIED_TOOLS or spec.safety in {Safety.PUBLIC, Safety.DANGER}:
        raise ValueError("Refused tool registration: " + spec.name)


class PeriodRule(Record):
    lanes: list[Literal["gpu", "intel", "build", "idle"]] = Field(default_factory=list)
    classes: list[Literal["ordinary", "whole"]] = Field(default_factory=list)
    max_hold_seconds: int = Field(default=480, ge=1, le=480)
    max_cpu_seconds: int = Field(default=480, ge=1, le=86400)


class Authorization(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lane: Literal["gpu", "intel", "build", "idle"]
    resource_class: Literal["ordinary", "whole", "exclusive", "priority"]
    expires_at: datetime
    max_hold_seconds: int = Field(ge=1, le=480)
    max_cpu_seconds: int = Field(default=480, ge=1, le=86400)


class OperatorPolicy(Record):
    windowless: Literal[True] = True
    day_start_hour: int = Field(default=7, ge=0, le=23)
    night_start_hour: int = Field(default=22, ge=0, le=23)
    day: PeriodRule = Field(default_factory=PeriodRule)
    night: PeriodRule = Field(default_factory=PeriodRule)
    authorizations: list[Authorization] = Field(default_factory=list, max_length=100)


class PolicySnapshot(Record):
    revision: str
    observed_at: str
    source: Literal["operator", "conservative_default", "invalid_operator_file"]
    windowless: bool = True
    period: Literal["day", "night"]
    rule: PeriodRule
    named_authorization_required: list[str]
    protected_root_count: int


class Policy:
    def __init__(self, config: Config):
        self.config = config
        self.path = config.state_root / "policy.json"

    def _load(self) -> tuple[OperatorPolicy, str, str]:
        try:
            if self.path.is_symlink():
                raise ValueError
            with self.path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            policy = OperatorPolicy.model_validate_json(raw)
            if policy.day_start_hour >= policy.night_start_hour:
                raise ValueError
            if any(a.expires_at.tzinfo is None for a in policy.authorizations):
                raise ValueError
            return policy, hashlib.sha256(raw).hexdigest(), "operator"
        except FileNotFoundError:
            return OperatorPolicy(), "default", "conservative_default"
        except (OSError, ValueError, ValidationError):
            return OperatorPolicy(), "invalid", "invalid_operator_file"

    def snapshot(self, *, now: datetime | None = None) -> PolicySnapshot:
        policy, revision, source = self._load()
        local = now or datetime.now().astimezone()
        period = "day" if policy.day_start_hour <= local.hour < policy.night_start_hour else "night"
        return PolicySnapshot(revision=revision, observed_at=utc_now(), source=source, period=period,
            rule=getattr(policy, period), named_authorization_required=["exclusive", "priority"],
            protected_root_count=len(self.config.protected_roots))

    def authorize(self, *, lane: str, resource_class: str, wall_seconds: float,
                  plan_hash: str, authorization: str | None = None, stage: Literal["plan", "admission"] = "plan") -> PolicySnapshot:
        # Deliberately reread for each call. A plan grants nothing at admission.
        policy, revision, source = self._load()
        local = datetime.now().astimezone()
        period = "day" if policy.day_start_hour <= local.hour < policy.night_start_hour else "night"
        rule = getattr(policy, period)
        limit = rule.max_cpu_seconds if lane in {"build", "idle"} else rule.max_hold_seconds
        allowed = lane in rule.lanes and resource_class in rule.classes and 0 < wall_seconds <= limit
        if authorization:
            allowed = any(a.id == authorization and a.plan_hash == plan_hash and a.lane == lane
                and a.resource_class == resource_class and a.expires_at > datetime.now(timezone.utc)
                and 0 < wall_seconds <= (a.max_cpu_seconds if lane in {"build", "idle"} else a.max_hold_seconds)
                for a in policy.authorizations)
        if not allowed or source != "operator":
            raise fail("refused_by_policy", "policy_" + stage)
        return PolicySnapshot(revision=revision, observed_at=utc_now(), source=source, period=period,
            rule=rule, named_authorization_required=["exclusive", "priority"],
            protected_root_count=len(self.config.protected_roots))


class PathGuards:
    def __init__(self, config: Config):
        self.read_roots = tuple(p.resolve() for p in config.read_roots)
        self.write_roots = tuple(p.resolve() for p in config.write_roots)
        self.protected = tuple(p.resolve() for p in config.protected_roots)

    def _check(self, path: Path, roots: tuple[Path, ...], operation: str) -> Path:
        if not path.is_absolute():
            raise fail("invalid_argument", operation)
        absolute = Path(os.path.abspath(path))
        canonical = absolute.resolve()
        if any(absolute.is_relative_to(p) or canonical.is_relative_to(p) for p in self.protected):
            raise fail("refused_by_policy", operation)
        if not any(absolute.is_relative_to(r) and canonical.is_relative_to(r) for r in roots):
            raise fail("refused_by_policy", operation)
        return canonical

    def read(self, path: Path) -> Path:
        result = self._check(path, self.read_roots + self.write_roots, "path_read")
        if not result.exists():
            raise fail("not_found", "path_read")
        return result

    def output(self, path: Path) -> Path:
        result = self._check(path, self.write_roots, "path_output")
        if path.is_symlink() or result.exists():
            raise fail("refused_by_policy", "path_output")
        return result

    def reserve_file(self, path: Path) -> int:
        """Create exactly one new file; reject symlinks in every directory component."""
        target = self.output(path)
        fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in target.parts[1:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        except OSError:
            raise fail("refused_by_policy", "path_output") from None
        finally:
            os.close(fd)


def validate_state_root(config: Config) -> None:
    root = config.state_root
    if not root.is_absolute() or any(root.resolve().is_relative_to(p.resolve()) for p in config.protected_roots):
        raise ValueError("State root must be an absolute unprotected directory")
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("State root must be outside repositories")
    if root.is_symlink():
        raise ValueError("State root must not be a symlink")
    if root.exists() and (root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) & 0o022):
        raise ValueError("State root must be owned by this user and not writable by others")


CHILD_ENV_ALLOWLIST = frozenset({"LANG", "LC_ALL", "TZ"})


def child_environment(job_dir: Path, inherited: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if inherited is None else inherited
    env = {key: value for key, value in source.items() if key in CHILD_ENV_ALLOWLIST}
    env.update({"PATH": os.defpath, "PYTHONUNBUFFERED": "1"})
    for name, subdir in {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                         "XDG_DATA_HOME": "data", "XDG_STATE_HOME": "state", "TMPDIR": "tmp"}.items():
        path = job_dir / subdir
        path.mkdir(mode=0o700, exist_ok=True)
        env[name] = str(path)
    return env
