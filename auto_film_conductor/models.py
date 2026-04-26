from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class RoundStatus(StrEnum):
    COLLECTING = "collecting"
    PAUSED = "paused"
    APPROVAL_OPEN = "approval_open"
    RCV_OPEN = "rcv_open"
    DOWNLOADING = "downloading"
    PLAYING = "playing"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class SuggestionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PollKind(StrEnum):
    APPROVAL = "approval"
    RCV = "rcv"


class PollStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Round(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(index=True)
    previous_status: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    collection_closes_at: datetime
    sample_size: int
    runoff_size: int
    approval_poll_seconds: int
    rcv_poll_seconds: int
    approval_poll_id: str | None = None
    rcv_poll_id: str | None = None
    winner_suggestion_id: int | None = None
    winner_title: str | None = None
    winner_file_path: str | None = None


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    round_id: int = Field(index=True)
    platform: str = Field(index=True)
    user_id: str = Field(index=True)
    display_name: str
    raw_text: str
    status: str = Field(index=True)
    rejection_reason: str | None = None
    title: str | None = None
    year: int | None = None
    movie_key: str | None = Field(default=None, index=True)
    tmdb_id: int | None = None
    radarr_id: int | None = None
    overview: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    sampled: bool = False


class PollRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    round_id: int = Field(index=True)
    kind: str = Field(index=True)
    external_id: str = Field(index=True)
    status: str = Field(index=True)
    candidate_suggestion_ids: str
    created_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    round_id: int | None = Field(default=None, index=True)
    event_type: str = Field(index=True)
    message: str
    created_at: datetime = Field(default_factory=utc_now)
