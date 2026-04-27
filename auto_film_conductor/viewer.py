from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel
from sqlmodel import Session, col, select

from auto_film_conductor.domain import DownloadProgress
from auto_film_conductor.models import PollRecord, Round, RoundStatus, Suggestion, SuggestionStatus, utc_now


class ViewerSuggestion(BaseModel):
    id: int
    title: str
    year: int | None
    display_name: str
    created_at: datetime
    sampled: bool


class ViewerPoll(BaseModel):
    kind: str
    candidates: list[ViewerSuggestion]


class ViewerDownload(BaseModel):
    title: str | None
    percent: float | None
    estimated_completion_time: str | None
    time_left: str | None
    message: str


class ViewerRound(BaseModel):
    id: int
    status: str
    phase: str
    timer_target: datetime | None
    seconds_remaining: int | None
    suggestions: list[ViewerSuggestion]
    poll: ViewerPoll | None
    winner_title: str | None
    download: ViewerDownload | None


class ViewerStateResponse(BaseModel):
    server_time: datetime
    round: ViewerRound | None


class DownloadProgressProvider(Protocol):
    async def download_progress(self, movie_id: int) -> DownloadProgress | None:
        ...


async def build_viewer_state(
    session: Session,
    *,
    progress_provider: DownloadProgressProvider | None = None,
) -> ViewerStateResponse:
    now = utc_now()
    round_record = _current_round(session)
    if round_record is None:
        return ViewerStateResponse(server_time=now, round=None)

    suggestions = _accepted_suggestions(session, round_record.id)
    poll = _viewer_poll(session, round_record, suggestions)
    timer_target = _timer_target(session, round_record)
    download = await _viewer_download(session, round_record, progress_provider)

    return ViewerStateResponse(
        server_time=now,
        round=ViewerRound(
            id=_require_id(round_record),
            status=round_record.status,
            phase=_phase_label(round_record),
            timer_target=timer_target,
            seconds_remaining=_seconds_remaining(now, timer_target),
            suggestions=[_viewer_suggestion(suggestion) for suggestion in suggestions],
            poll=poll,
            winner_title=round_record.winner_title,
            download=download,
        ),
    )


def _current_round(session: Session) -> Round | None:
    return session.exec(
        select(Round)
        .where(~col(Round.status).in_([RoundStatus.CANCELLED, RoundStatus.COMPLETED]))
        .order_by(col(Round.created_at).desc())
    ).first()


def _accepted_suggestions(session: Session, round_id: int | None) -> list[Suggestion]:
    if round_id is None:
        return []
    return list(
        session.exec(
            select(Suggestion)
            .where(Suggestion.round_id == round_id, Suggestion.status == SuggestionStatus.ACCEPTED)
            .order_by(col(Suggestion.created_at), col(Suggestion.id))
        ).all()
    )


def _viewer_poll(session: Session, round_record: Round, suggestions: list[Suggestion]) -> ViewerPoll | None:
    if round_record.status not in {RoundStatus.APPROVAL_OPEN, RoundStatus.RCV_OPEN}:
        return None

    poll = _active_poll(session, round_record)
    if poll is None:
        return None

    candidate_ids = _candidate_ids(poll)
    suggestions_by_id = {suggestion.id: suggestion for suggestion in suggestions}
    candidates = [
        _viewer_suggestion(suggestions_by_id[suggestion_id])
        for suggestion_id in candidate_ids
        if suggestion_id in suggestions_by_id
    ]
    return ViewerPoll(kind=poll.kind, candidates=candidates)


def _active_poll(session: Session, round_record: Round) -> PollRecord | None:
    external_id = round_record.approval_poll_id if round_record.status == RoundStatus.APPROVAL_OPEN else round_record.rcv_poll_id
    if external_id is None:
        return None
    return session.exec(select(PollRecord).where(PollRecord.external_id == external_id)).first()


def _candidate_ids(poll: PollRecord) -> list[int]:
    try:
        return [int(value) for value in json.loads(poll.candidate_suggestion_ids)]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _timer_target(session: Session, round_record: Round) -> datetime | None:
    if round_record.status == RoundStatus.COLLECTING:
        return _as_utc(round_record.collection_closes_at)
    if round_record.status not in {RoundStatus.APPROVAL_OPEN, RoundStatus.RCV_OPEN}:
        return None

    poll = _active_poll(session, round_record)
    if poll is None:
        return None

    duration = round_record.approval_poll_seconds if round_record.status == RoundStatus.APPROVAL_OPEN else round_record.rcv_poll_seconds
    return _as_utc(poll.created_at) + timedelta(seconds=duration)


async def _viewer_download(
    session: Session,
    round_record: Round,
    progress_provider: DownloadProgressProvider | None,
) -> ViewerDownload | None:
    if round_record.status != RoundStatus.DOWNLOADING:
        return None

    winner_title = round_record.winner_title
    winner = session.get(Suggestion, round_record.winner_suggestion_id) if round_record.winner_suggestion_id else None
    if winner is None or winner.radarr_id is None or progress_provider is None:
        return ViewerDownload(
            title=winner_title,
            percent=None,
            estimated_completion_time=None,
            time_left=None,
            message="Waiting for Radarr import",
        )

    try:
        progress = await progress_provider.download_progress(winner.radarr_id)
    except Exception:
        progress = None

    if progress is None:
        return ViewerDownload(
            title=winner_title,
            percent=None,
            estimated_completion_time=None,
            time_left=None,
            message="Waiting for Radarr import",
        )

    return ViewerDownload(
        title=winner_title or progress.title,
        percent=progress.percent,
        estimated_completion_time=progress.estimated_completion_time,
        time_left=progress.time_left,
        message=_download_message(progress),
    )


def _download_message(progress: DownloadProgress) -> str:
    status = (progress.status or "").casefold()
    tracked_state = (progress.tracked_download_state or "").casefold()
    tracked_status = (progress.tracked_download_status or "").casefold()
    if "import" in tracked_state or "import" in tracked_status:
        return "Importing into library"
    if status in {"downloading", "downloadclientunavailable"} or progress.percent is not None:
        return "Downloading via Radarr"
    if status:
        return "Radarr is preparing the winner"
    return "Waiting for Radarr import"


def _viewer_suggestion(suggestion: Suggestion) -> ViewerSuggestion:
    return ViewerSuggestion(
        id=_require_id(suggestion),
        title=suggestion.title or "Accepted suggestion",
        year=suggestion.year,
        display_name=suggestion.display_name,
        created_at=_as_utc(suggestion.created_at),
        sampled=suggestion.sampled,
    )


def _phase_label(round_record: Round) -> str:
    labels = {
        RoundStatus.COLLECTING.value: "Suggestions open",
        RoundStatus.PAUSED.value: "Paused",
        RoundStatus.APPROVAL_OPEN.value: "Approval vote",
        RoundStatus.RCV_OPEN.value: "Runoff vote",
        RoundStatus.DOWNLOADING.value: "Downloading winner",
        RoundStatus.PLAYING.value: "Now playing",
        RoundStatus.CANCELLED.value: "Cancelled",
        RoundStatus.COMPLETED.value: "Completed",
    }
    return labels.get(round_record.status, str(round_record.status))


def _seconds_remaining(now: datetime, target: datetime | None) -> int | None:
    if target is None:
        return None
    return max(0, math.ceil((_as_utc(target) - _as_utc(now)).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_id(record: Round | Suggestion) -> int:
    if record.id is None:
        raise ValueError("Expected persisted record")
    return record.id
