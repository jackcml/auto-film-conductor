from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from auto_film_conductor.config import Settings
from auto_film_conductor.domain import Downloader, MovieResolver, PlayerController, PollCandidate, ResolvedMovie, VotingProvider
from auto_film_conductor.models import AuditEvent, PollKind, PollRecord, PollStatus, Round, RoundStatus, Suggestion, SuggestionStatus, utc_now


@dataclass(frozen=True)
class SuggestionResult:
    accepted: bool
    message: str
    suggestion: Suggestion | None = None


class ConductorService:
    def __init__(
        self,
        *,
        session_factory,
        settings: Settings,
        resolver: MovieResolver,
        voting: VotingProvider,
        downloader: Downloader,
        player: PlayerController,
        rng: random.Random | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.resolver = resolver
        self.voting = voting
        self.downloader = downloader
        self.player = player
        self.rng = rng or random.Random()

    async def start_round(self) -> Round:
        with self.session_factory() as session:
            active = _current_round(session)
            if active is not None:
                raise ValueError(f"Round {active.id} is already active")
            now = utc_now()
            round_record = Round(
                status=RoundStatus.COLLECTING,
                collection_closes_at=now + timedelta(seconds=self.settings.suggestion_window_seconds),
                sample_size=self.settings.sample_size,
                runoff_size=self.settings.runoff_size,
                approval_poll_seconds=self.settings.approval_poll_seconds,
                rcv_poll_seconds=self.settings.rcv_poll_seconds,
            )
            session.add(round_record)
            session.commit()
            session.refresh(round_record)
            _audit(session, round_record.id, "round_started", f"Started round {round_record.id}")
            session.commit()
            return round_record

    async def current_round(self) -> Round | None:
        with self.session_factory() as session:
            return _current_round(session)

    async def submit_suggestion(self, *, platform: str, user_id: str, display_name: str, raw_text: str) -> SuggestionResult:
        cleaned = raw_text.strip()
        with self.session_factory() as session:
            round_record = _require_current_round(session)
            if round_record.status != RoundStatus.COLLECTING:
                return SuggestionResult(False, f"Suggestions are not open; current state is {round_record.status}.")
            if not cleaned:
                return SuggestionResult(False, "Send a movie title after mentioning the bot.")
            existing_user_suggestion = session.exec(
                select(Suggestion).where(
                    Suggestion.round_id == round_record.id,
                    Suggestion.platform == platform,
                    Suggestion.user_id == user_id,
                    Suggestion.status == SuggestionStatus.ACCEPTED,
                )
            ).first()
            if existing_user_suggestion is not None:
                return SuggestionResult(False, "You already have one active suggestion in this round.")

        movie = await self.resolver.resolve(cleaned)
        with self.session_factory() as session:
            round_record = _require_current_round(session)
            if movie is None:
                suggestion = Suggestion(
                    round_id=round_record.id,
                    platform=platform,
                    user_id=user_id,
                    display_name=display_name,
                    raw_text=cleaned,
                    status=SuggestionStatus.REJECTED,
                    rejection_reason="No matching movie found.",
                )
                session.add(suggestion)
                _audit(session, round_record.id, "suggestion_rejected", f"{display_name}: {cleaned}")
                session.commit()
                return SuggestionResult(False, "I could not find a matching movie.", suggestion)

            duplicate = session.exec(
                select(Suggestion).where(
                    Suggestion.round_id == round_record.id,
                    Suggestion.movie_key == movie.movie_key,
                    Suggestion.status == SuggestionStatus.ACCEPTED,
                )
            ).first()
            if duplicate is not None:
                return SuggestionResult(False, f"{movie.title} is already in the pool.")

            suggestion = Suggestion(
                round_id=round_record.id,
                platform=platform,
                user_id=user_id,
                display_name=display_name,
                raw_text=cleaned,
                status=SuggestionStatus.ACCEPTED,
                title=movie.title,
                year=movie.year,
                movie_key=movie.movie_key,
                tmdb_id=movie.tmdb_id,
                radarr_id=movie.radarr_id,
                overview=movie.overview,
            )
            session.add(suggestion)
            _audit(session, round_record.id, "suggestion_accepted", f"{display_name}: {movie.title}")
            session.commit()
            session.refresh(suggestion)
            return SuggestionResult(True, f"Added {suggestion.title} ({suggestion.year}).", suggestion)

    async def close_collection(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _require_status(round_record, RoundStatus.COLLECTING)
            suggestions = session.exec(
                select(Suggestion).where(Suggestion.round_id == round_id, Suggestion.status == SuggestionStatus.ACCEPTED)
            ).all()
            if not suggestions:
                raise ValueError("Cannot create a poll without accepted suggestions")
            sampled = list(suggestions)
            self.rng.shuffle(sampled)
            sampled = sampled[: round_record.sample_size]
            for suggestion in sampled:
                suggestion.sampled = True
                session.add(suggestion)
            session.commit()

        candidates = [_candidate(suggestion) for suggestion in sampled]
        poll_id = await self.voting.create_poll(
            kind=PollKind.APPROVAL,
            title="Movie Night Approval Vote",
            candidates=candidates,
            duration_seconds=round_record.approval_poll_seconds,
        )

        with self.session_factory() as session:
            round_record = _round(session, round_id)
            poll = PollRecord(
                round_id=round_id,
                kind=PollKind.APPROVAL,
                external_id=poll_id,
                status=PollStatus.OPEN,
                candidate_suggestion_ids=json.dumps([suggestion.id for suggestion in sampled]),
            )
            round_record.approval_poll_id = poll_id
            round_record.status = RoundStatus.APPROVAL_OPEN
            round_record.updated_at = utc_now()
            session.add(poll)
            session.add(round_record)
            _audit(session, round_id, "approval_poll_opened", poll_id)
            session.commit()
            session.refresh(round_record)
            return round_record

    async def reroll(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _require_status(round_record, RoundStatus.APPROVAL_OPEN)
            old_poll_id = _require_value(round_record.approval_poll_id, "No approval poll exists")
            suggestions = session.exec(
                select(Suggestion).where(Suggestion.round_id == round_id, Suggestion.status == SuggestionStatus.ACCEPTED)
            ).all()
            if not suggestions:
                raise ValueError("Cannot reroll without accepted suggestions")
            sampled = list(suggestions)
            self.rng.shuffle(sampled)
            sampled = sampled[: round_record.sample_size]
            for suggestion in suggestions:
                suggestion.sampled = suggestion in sampled
                session.add(suggestion)
            session.commit()

        await self.voting.close_poll(old_poll_id)
        poll_id = await self.voting.create_poll(
            kind=PollKind.APPROVAL,
            title="Movie Night Approval Vote",
            candidates=[_candidate(suggestion) for suggestion in sampled],
            duration_seconds=round_record.approval_poll_seconds,
        )

        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _close_poll_record(session, old_poll_id)
            poll = PollRecord(
                round_id=round_id,
                kind=PollKind.APPROVAL,
                external_id=poll_id,
                status=PollStatus.OPEN,
                candidate_suggestion_ids=json.dumps([suggestion.id for suggestion in sampled]),
            )
            round_record.approval_poll_id = poll_id
            round_record.updated_at = utc_now()
            session.add(poll)
            session.add(round_record)
            _audit(session, round_id, "approval_poll_rerolled", poll_id)
            session.commit()
            session.refresh(round_record)
            return round_record

    async def close_approval(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _require_status(round_record, RoundStatus.APPROVAL_OPEN)
            approval_poll_id = _require_value(round_record.approval_poll_id, "No approval poll exists")

        await self.voting.close_poll(approval_poll_id)
        result = await self.voting.get_results(approval_poll_id)
        runoff_ids = [int(candidate_id) for candidate_id in result.ranked_candidate_ids[: self.settings.runoff_size]]
        if not runoff_ids:
            raise ValueError("Approval poll had no runoff candidates")

        with self.session_factory() as session:
            suggestions = session.exec(select(Suggestion).where(col(Suggestion.id).in_(runoff_ids))).all()
            suggestions_by_id = {suggestion.id: suggestion for suggestion in suggestions}
            ordered = [suggestions_by_id[suggestion_id] for suggestion_id in runoff_ids if suggestion_id in suggestions_by_id]
            round_record = _round(session, round_id)

        poll_id = await self.voting.create_poll(
            kind=PollKind.RCV,
            title="Movie Night Ranked-Choice Runoff",
            candidates=[_candidate(suggestion) for suggestion in ordered],
            duration_seconds=round_record.rcv_poll_seconds,
        )

        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _close_poll_record(session, approval_poll_id)
            poll = PollRecord(
                round_id=round_id,
                kind=PollKind.RCV,
                external_id=poll_id,
                status=PollStatus.OPEN,
                candidate_suggestion_ids=json.dumps(runoff_ids),
            )
            round_record.rcv_poll_id = poll_id
            round_record.status = RoundStatus.RCV_OPEN
            round_record.updated_at = utc_now()
            session.add(poll)
            session.add(round_record)
            _audit(session, round_id, "rcv_poll_opened", poll_id)
            session.commit()
            session.refresh(round_record)
            return round_record

    async def close_rcv_and_play(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _require_status(round_record, RoundStatus.RCV_OPEN)
            rcv_poll_id = _require_value(round_record.rcv_poll_id, "No RCV poll exists")

        await self.voting.close_poll(rcv_poll_id)
        result = await self.voting.get_results(rcv_poll_id)
        if result.winner_candidate_id is None:
            raise ValueError("RCV poll did not produce a winner")
        winner_id = int(result.winner_candidate_id)

        with self.session_factory() as session:
            winner = session.get(Suggestion, winner_id)
            if winner is None:
                raise ValueError(f"Winner suggestion {winner_id} does not exist")
            movie = ResolvedMovie(
                title=_require_value(winner.title, "Winner has no title"),
                year=winner.year,
                tmdb_id=winner.tmdb_id,
                radarr_id=winner.radarr_id,
                overview=winner.overview,
            )
            round_record = _round(session, round_id)
            round_record.status = RoundStatus.DOWNLOADING
            round_record.winner_suggestion_id = winner_id
            round_record.winner_title = movie.title
            round_record.updated_at = utc_now()
            _close_poll_record(session, rcv_poll_id)
            session.add(round_record)
            _audit(session, round_id, "winner_selected", movie.title)
            session.commit()

        imported = await self.downloader.request_and_wait(movie)
        if not imported.file_path:
            raise ValueError("Downloader completed without a file path")
        await self.player.load(imported.file_path)

        with self.session_factory() as session:
            round_record = _round(session, round_id)
            round_record.status = RoundStatus.PLAYING
            round_record.winner_file_path = imported.file_path
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "playback_started", imported.file_path)
            session.commit()
            session.refresh(round_record)
            return round_record

    async def pause(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            if round_record.status == RoundStatus.PAUSED:
                return round_record
            round_record.previous_status = round_record.status
            round_record.status = RoundStatus.PAUSED
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "round_paused", round_record.previous_status or "")
            session.commit()
            session.refresh(round_record)
            return round_record

    async def resume(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            _require_status(round_record, RoundStatus.PAUSED)
            round_record.status = round_record.previous_status or RoundStatus.COLLECTING
            round_record.previous_status = None
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "round_resumed", round_record.status)
            session.commit()
            session.refresh(round_record)
            return round_record

    async def cancel(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            round_record.status = RoundStatus.CANCELLED
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "round_cancelled", "")
            session.commit()
            session.refresh(round_record)
            return round_record

    async def complete(self, round_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            round_record.status = RoundStatus.COMPLETED
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "round_completed", "")
            session.commit()
            session.refresh(round_record)
            return round_record

    async def override_winner(self, round_id: int, suggestion_id: int) -> Round:
        with self.session_factory() as session:
            round_record = _round(session, round_id)
            suggestion = session.get(Suggestion, suggestion_id)
            if suggestion is None or suggestion.round_id != round_id or suggestion.status != SuggestionStatus.ACCEPTED:
                raise ValueError("Override target must be an accepted suggestion from this round")
            round_record.winner_suggestion_id = suggestion_id
            round_record.winner_title = suggestion.title
            round_record.updated_at = utc_now()
            session.add(round_record)
            _audit(session, round_id, "winner_overridden", suggestion.title or str(suggestion_id))
            session.commit()
            session.refresh(round_record)
            return round_record

    async def stop_playback(self) -> None:
        await self.player.stop()


def _current_round(session: Session) -> Round | None:
    return session.exec(
        select(Round)
        .where(~col(Round.status).in_([RoundStatus.CANCELLED, RoundStatus.COMPLETED]))
        .order_by(col(Round.created_at).desc())
    ).first()


def _require_current_round(session: Session) -> Round:
    round_record = _current_round(session)
    if round_record is None:
        raise ValueError("No active round")
    return round_record


def _round(session: Session, round_id: int) -> Round:
    round_record = session.get(Round, round_id)
    if round_record is None:
        raise ValueError(f"Round {round_id} does not exist")
    return round_record


def _require_status(round_record: Round, expected: RoundStatus) -> None:
    if round_record.status != expected:
        raise ValueError(f"Expected round {round_record.id} to be {expected}, got {round_record.status}")


def _candidate(suggestion: Suggestion) -> PollCandidate:
    if suggestion.id is None:
        raise ValueError("Suggestion must be persisted before polling")
    return PollCandidate(id=str(suggestion.id), title=suggestion.title or suggestion.raw_text, year=suggestion.year)


def _close_poll_record(session: Session, external_id: str) -> None:
    poll = session.exec(select(PollRecord).where(PollRecord.external_id == external_id)).first()
    if poll is not None:
        poll.status = PollStatus.CLOSED
        poll.closed_at = datetime.now(UTC)
        session.add(poll)


def _audit(session: Session, round_id: int | None, event_type: str, message: str) -> None:
    session.add(AuditEvent(round_id=round_id, event_type=event_type, message=message))


def _require_value(value, message: str):
    if value is None:
        raise ValueError(message)
    return value
