"""Operator registrations and bounded knowledge records; no machine-specific defaults."""
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field, field_validator, model_validator
from src.models import Record, ErrorEnvelope

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReportRegistration(Record):
    id: Name
    file: str
    privacy: Literal["private", "public_candidate"] = "private"

    @field_validator("file")
    @classmethod
    def relative_document(cls, value):
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in {".md", ".txt"}:
            raise ValueError("A report must be a relative text document")
        return value


class KnowledgeConfig(Record):
    """Set Config.knowledge outside MCP. Paths are trusted registrations, not tool arguments.

    reader_contract is an operator attestation of an upstream CLI implementing
    --read-only (GET only, no room joins, no configuration save), --room and JSON.
    Stock readers do not implement it and remain unavailable. No account settings
    are opened by this package. Skills/reports are explicit, private registrations.
    """
    gh: Path | None = None
    repositories: tuple[str, ...] = Field(default=(), max_length=32)
    skills_dir: Path | None = None
    skill_names: tuple[Name, ...] = Field(default=(), max_length=100)
    reports_dir: Path | None = None
    reports: tuple[ReportRegistration, ...] = Field(default=(), max_length=500)
    posted_report_id: Name | None = None
    pr_index_report_id: Name | None = None
    prloop_script: Path | None = None
    prloop_state_dir: Path | None = None
    nightly_root: Path | None = None
    nightly_database: Path | None = None
    rooms: tuple[str, ...] = Field(default=(), max_length=32)
    request_room: str | None = None
    reader_contract: Literal["get-only-no-save-v1"] | None = None
    max_pages: int = Field(default=30, ge=1, le=100)
    read_timeout_seconds: float = Field(default=60, ge=0.05, le=300)
    watch_interval_seconds: float = Field(default=30, ge=0.05, le=300)
    snapshot_max_age_seconds: int = Field(default=600, ge=1, le=86400)

    @field_validator("gh", "skills_dir", "reports_dir", "prloop_script", "prloop_state_dir", "nightly_root", "nightly_database")
    @classmethod
    def absolute_path(cls, value):
        if value is not None and not value.is_absolute():
            raise ValueError("Knowledge paths must be absolute")
        return value


    @classmethod
    def from_file(cls, path: Path) -> "KnowledgeConfig":
        """Load a bounded operator document, never an account/session file."""
        if not path.is_absolute():
            raise ValueError("Knowledge configuration must be absolute")
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            return cls.model_validate_json(raw)
        except (OSError, ValueError):
            raise ValueError("Invalid knowledge configuration") from None
    @field_validator("repositories")
    @classmethod
    def valid_repositories(cls, values):
        import re
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", v) or ".." in v for v in values):
            raise ValueError("Invalid repository allowlist")
        return values

    @model_validator(mode="after")
    def unique_registrations(self):
        if len({r.id for r in self.reports}) != len(self.reports) or len(set(self.skill_names)) != len(self.skill_names):
            raise ValueError("Duplicate knowledge registration")
        if self.request_room is not None and self.request_room not in self.rooms:
            raise ValueError("The request room must be allowlisted")
        return self


class UntrustedText(Record):
    text: str | None
    truncated: bool = False
    untrusted: Literal[True] = True


class ThreadRef(Record):
    repository: str = Field(max_length=200)
    number: int = Field(ge=1, le=2147483647)


class ThreadItem(Record):
    id: int | None = None
    body: UntrustedText
    author: UntrustedText
    updated_at: str | None = None
    url: str | None = None
    state: UntrustedText | None = None
    path: UntrustedText | None = None
    diff_hunk: UntrustedText | None = None
    untrusted: Literal[True] = True


class ThreadSnapshot(Record):
    repository: str
    number: int
    kind: Literal["issue", "pull_request"]
    head: str | None
    title: UntrustedText
    body: UntrustedText
    state: UntrustedText
    comments: list[ThreadItem]
    reviews: list[ThreadItem]
    inline: list[ThreadItem]
    pages_complete: bool
    consistent: bool | None
    updated_at: str | None
    observed_at: str
    snapshot_hash: str
    next_cursor: str | None
    items_total: int
    items_truncated: bool
    warnings: list[str]
    untrusted: Literal[True] = True


class ReaderResult(Record):
    available: bool
    reason: str | None = None
    error: ErrorEnvelope | None = None
    room_id: str | None = None
    cards: list[dict] | None = None
    messages: list[dict] | None = None
    next_cursor: str | None = None
    complete: bool | None = None
    unknown: list[str] = Field(default_factory=list)
    observed_at: str
    untrusted: Literal[True] = True


class SearchMatch(Record):
    resource_uri: str
    lines: tuple[int, int]
    revision: str
    privacy: Literal["private", "public_candidate"]
    excerpt: UntrustedText


class SearchResult(Record):
    matches: list[SearchMatch]
    next_cursor: str | None
    index_revision: str
    stale_warnings: list[str]


class DocumentSection(Record):
    resource_uri: str
    revision: str
    privacy: Literal["private", "public_candidate"]
    lines: list[dict]
    next_line: int | None
    next_uri: str | None
    total_lines: int
    truncated: bool
    stale_warnings: list[str]
    untrusted: Literal[True] = True
