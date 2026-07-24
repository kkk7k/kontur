from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventStatus(StrEnum):
    NEW = "new"
    NOTIFIED = "notified"
    SEEN = "seen"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    SUPPRESSED = "suppressed"
    DELIVERY_FAILED = "delivery_failed"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    uri: str | None = None


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    title: str
    uri: str
    content_type: str | None = None
    checksum: str | None = None


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=4, max_length=80)
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime
    producer: str = Field(min_length=1, max_length=80)
    agent: str | None = Field(default=None, max_length=80)
    run_id: str | None = Field(default=None, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    severity: Severity = Severity.INFO
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_action: bool = False
    approval_required: bool = False
    recommended_action: str | None = Field(default=None, max_length=1000)
    deduplication_key: str = Field(min_length=1, max_length=200)
    source: Source | None = None
    metrics: dict[str, int | float | str | None] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactInput] = Field(default_factory=list)
    correlation_id: str | None = Field(default=None, max_length=100)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include timezone")
        return value


class EventView(EventCreate):
    received_at: datetime
    status: EventStatus


class EventList(BaseModel):
    items: list[EventView]
    total: int


class StatusView(BaseModel):
    service: str = "kontur"
    status: str
    database: str
    pending_inbox: int
    failed_deliveries: int
    latest_event_at: datetime | None = None


class VacancyItem(BaseModel):
    source: str
    external_id: str
    title: str
    url: str
    company: str | None = None
    experience_id: str | None = None
    experience_name: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str | None = None
    published_at: datetime
    matched_by: list[str] = Field(default_factory=list)


class VacancyCollectionResult(BaseModel):
    source: str
    fetched: int
    qualified: int
    created: int
    duplicate: int
    bootstrapped: bool
