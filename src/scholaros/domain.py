from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ProjectStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class Stage(StrEnum):
    SCOPING = "scoping"
    SEARCHING = "searching"
    SYNTHESIZING = "synthesizing"
    DESIGNING = "designing"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    REVISING = "revising"
    COMPLETED = "completed"


class SearchField(StrEnum):
    ALL = "all"
    TITLE = "title"
    AUTHOR = "author"
    DOI = "doi"
    VENUE = "venue"


@dataclass(frozen=True, slots=True)
class SearchQueryPlan:
    input_query: str
    search_query: str
    search_terms: list[str]
    field: SearchField
    natural_language: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_query": self.input_query,
            "search_query": self.search_query,
            "search_terms": list(self.search_terms),
            "field": self.field.value,
            "natural_language": self.natural_language,
        }


@dataclass(slots=True)
class ResearchSpec:
    title: str
    question: str
    contribution: str
    hypotheses: list[str]
    keywords: list[str]
    paper_type: str = "empirical"
    target_audience: str = "AI 与科研工具研究者"
    source_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Paper:
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    sources: list[str]
    external_id: str
    doi: str | None = None
    venue: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    is_open_access: bool = False
    citation_count: int | None = None
    score: float = 0.0
    cite_key: str | None = None
    author_affiliations: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Paper:
        return cls(**value)


@dataclass(slots=True)
class Evidence:
    cite_key: str
    paper_title: str
    summary: str
    supports: list[str]
    caveats: list[str]
    source_locator: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceFailure:
    source: str
    reason: str
    suggestion: str = "稍后重试；若持续失败，可暂时取消该来源并使用其他来源。"
    retryable: bool = False
    status_code: int | None = None
    retry_after_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SearchResult:
    papers: list[Paper] = field(default_factory=list)
    failures: list[SourceFailure] = field(default_factory=list)
    filtered_out: int = 0


@dataclass(slots=True)
class ReviewFinding:
    severity: str
    code: str
    message: str
    suggestion: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class ReviewReport:
    score: int
    passed: bool
    findings: list[ReviewFinding]
    metrics: dict[str, int | float | str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "findings": [item.to_dict() for item in self.findings],
            "metrics": self.metrics,
        }


@dataclass(slots=True)
class Project:
    id: str
    idea: str
    title: str | None = None
    status: ProjectStatus = ProjectStatus.CREATED
    stage: Stage = Stage.SCOPING
    selected_sources: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["stage"] = self.stage.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Project:
        copy = dict(value)
        copy["status"] = ProjectStatus(copy["status"])
        copy["stage"] = Stage(copy["stage"])
        return cls(**copy)


@dataclass(slots=True)
class Event:
    type: str
    project_id: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utc_now)
