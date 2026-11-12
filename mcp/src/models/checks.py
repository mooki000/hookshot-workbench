"""Typed checks catalogue and bounded wire records.

The catalogue is operator-owned configuration, never a tool argument. Reviews bind
exact commits; registering a path is not a sandbox or an executable-code review.
"""
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field
from src.models import Artifact, Record

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TestName = Annotated[str, Field(pattern=r"^(tests|nx2)/test_[A-Za-z0-9_.-]+\.py$", max_length=240)]


class Checkout(Record):
    path: Path
    reviewed_commits: list[Sha] = Field(max_length=1000)


class Profile(Record):
    cpm: Path
    toolchain_identity: Digest
    profile_inputs: dict[str, Path] = Field(default_factory=dict, max_length=20)
    exefs: Path | None = None
    romfs: Path | None = None
    jobs: int = Field(default=8, ge=1, le=16)
    lift_jobs: int = Field(default=8, ge=1, le=8)
    wall_seconds: int = Field(default=480, ge=1, le=86400)
    queue_seconds: int = Field(default=3600, ge=1, le=86400)


class Catalogue(Record):
    checkouts: dict[Name, Checkout] = Field(default_factory=dict, max_length=100)
    profiles: dict[Name, Profile] = Field(default_factory=dict, max_length=20)
    git: Path
    copy_program: Path
    flock: Path
    idle: Path | None = None
    nocore: Path | None = None
    full_build_lock: Path | None = None
    windows_check: Path | None = None
    windows_sysroot: Path | None = None
    pr_check: Path | None = None
    pr_check_checkout: Path | None = None
    ci_reader: Path | None = None
    gh: Path | None = None
    repositories: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]] = Field(default_factory=list, max_length=20)
    approved_plans: list[Digest] = Field(default_factory=list, max_length=100)


class Plan(Record):
    repo_id: Name
    commit: Sha
    profile_id: Name
    mode: Literal["full", "reuse"]
    donor_id: str | None = None
    profile_identity: Digest
    lift_group: Digest
    compatibility_evidence: list[str] = Field(max_length=20)
    changed_assets: list[str] = Field(max_length=100)
    changed_assets_truncated: bool = False
    required_gates: list[str]
    expected_outputs: list[str] = ["hookshot", "hookshot_vk.so", "completion_receipt"]
    estimated_time: None = None
    dry_run: dict[str, int | str | None]
    plan_hash: Digest


class PlanResult(Record):
    plan: Plan
    artifact: Artifact


class TestResult(Record):
    name: TestName
    status: Literal["passed", "failed", "skipped"]
    seconds: float | None = None
    also_fails_on_base: bool | None = None


class SourceResult(Record):
    commit: Sha
    base_commit: Sha | None = None
    passed: int | None
    failed: int | None
    skipped: int | None
    tests: list[TestResult] = Field(max_length=100)
    failing_names: list[TestName] = Field(max_length=100)
    truncated: bool
    exit: int
    coverage: Literal["suite", "named"]
    table_complete: bool
    artifacts: list[str] = Field(default_factory=list, max_length=10)


class ImportedCommit(Record):
    commit: Sha
    original: Sha


class PreflightLine(Record):
    level: Literal["INFO", "OK", "WARN", "FAIL"]
    text: str = Field(max_length=500)


class PreflightResult(Record):
    checks: list[PreflightLine] = Field(max_length=100)
    passed: bool | None = Field(serialization_alias="pass")
    fetch_status: Literal["completed", "failed", "unknown"]
    base_observed: str | None
    truncated: bool


class WindowsResult(Record):
    compiled: int
    failed: int
    unjudged: int
    commands: list[list[str]] = Field(max_length=100)
    commands_truncated: bool
    windows_runtime: Literal["not_run"] = "not_run"
    exit: int


class FailedTest(Record):
    name: str = Field(max_length=300)
    also_red_on_main: Literal["yes", "unknown"]
    main_head: str | None = None


class CICheck(Record):
    name: str = Field(max_length=200)
    conclusion: str | None = Field(max_length=80)
    url: str | None = Field(max_length=500)
    failed_tests: list[FailedTest] = Field(max_length=100)
    unknown: str | None = None
    truncated: bool = False


class CIResult(Record):
    repository: str
    pull_request: int | None
    run_id: int | None
    head: str | None
    review_state: str | None
    checks: list[CICheck] = Field(max_length=100)
    next_cursor: int | None
    unknown: list[str] = Field(max_length=20)
    observed_at: str
    untrusted: Literal[True] = True
