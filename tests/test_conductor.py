from __future__ import annotations

import random
from pathlib import Path

import pytest
from sqlmodel import Session

from auto_film_conductor.config import Settings
from auto_film_conductor.domain import ResolvedMovie
from auto_film_conductor.models import PollRecord, RoundStatus, Suggestion
from auto_film_conductor.services.conductor import ConductorService
from auto_film_conductor.storage import init_db, make_engine
from auto_film_conductor.voting import MockVotingProvider


class FakeResolver:
    def __init__(self, movies: dict[str, ResolvedMovie]) -> None:
        self.movies = movies

    async def resolve(self, query: str) -> ResolvedMovie | None:
        return self.movies.get(query)


class FakeDownloader:
    def __init__(self, file_path: str = "C:/movies/winner.mkv") -> None:
        self.file_path = file_path
        self.requested: list[ResolvedMovie] = []

    async def request_and_wait(self, movie: ResolvedMovie) -> ResolvedMovie:
        self.requested.append(movie)
        return ResolvedMovie(
            title=movie.title,
            year=movie.year,
            tmdb_id=movie.tmdb_id,
            radarr_id=movie.radarr_id,
            overview=movie.overview,
            file_path=self.file_path,
        )


class FakePlayer:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.stopped = False

    async def load(self, file_path: str) -> None:
        self.loaded.append(file_path)

    async def stop(self) -> None:
        self.stopped = True


def make_service(workspace_tmp: Path):
    engine = make_engine(f"sqlite:///{workspace_tmp / 'test.db'}")
    init_db(engine)
    voting = MockVotingProvider()
    downloader = FakeDownloader()
    player = FakePlayer()
    resolver = FakeResolver(
        {
            "Shame 2012": ResolvedMovie("Shame", 2012, tmdb_id=76025, radarr_id=10),
            "Heat 1995": ResolvedMovie("Heat", 1995, tmdb_id=949, radarr_id=20),
            "Alien 1979": ResolvedMovie("Alien", 1979, tmdb_id=348, radarr_id=30),
        }
    )
    service = ConductorService(
        session_factory=lambda: Session(engine, expire_on_commit=False),
        settings=Settings(
            database_url=f"sqlite:///{workspace_tmp / 'test.db'}",
            sample_size=3,
            runoff_size=2,
            suggestion_window_seconds=300,
            approval_poll_seconds=300,
            rcv_poll_seconds=300,
        ),
        resolver=resolver,
        voting=voting,
        downloader=downloader,
        player=player,
        rng=random.Random(4),
    )
    return service, voting, downloader, player


@pytest.mark.asyncio
async def test_full_round_collects_votes_downloads_and_plays(workspace_tmp: Path) -> None:
    service, voting, downloader, player = make_service(workspace_tmp)

    round_record = await service.start_round()
    first = await service.submit_suggestion(platform="discord", user_id="1", display_name="One", raw_text="Shame 2012")
    second = await service.submit_suggestion(platform="discord", user_id="2", display_name="Two", raw_text="Heat 1995")
    third = await service.submit_suggestion(platform="discord", user_id="3", display_name="Three", raw_text="Alien 1979")

    assert first.accepted
    assert second.accepted
    assert third.accepted

    after_collection = await service.close_collection(round_record.id)
    assert after_collection.status == RoundStatus.APPROVAL_OPEN
    assert after_collection.approval_poll_id is not None

    approval = voting.snapshot(after_collection.approval_poll_id)
    heat_id = _candidate_id(approval.candidates, "Heat")
    shame_id = _candidate_id(approval.candidates, "Shame")
    alien_id = _candidate_id(approval.candidates, "Alien")
    await voting.cast_approval_vote(approval.id, "v1", [heat_id, shame_id])
    await voting.cast_approval_vote(approval.id, "v2", [heat_id])
    await voting.cast_approval_vote(approval.id, "v3", [alien_id])
    await voting.cast_approval_vote(approval.id, "v4", [alien_id])

    after_approval = await service.close_approval(round_record.id)
    assert after_approval.status == RoundStatus.RCV_OPEN
    assert after_approval.rcv_poll_id is not None

    rcv = voting.snapshot(after_approval.rcv_poll_id)
    runoff_ids = {candidate.id for candidate in rcv.candidates}
    assert runoff_ids == {heat_id, alien_id}

    await voting.cast_rcv_vote(rcv.id, "v1", [alien_id, heat_id])
    await voting.cast_rcv_vote(rcv.id, "v2", [alien_id, heat_id])
    await voting.cast_rcv_vote(rcv.id, "v3", [heat_id, alien_id])

    final_round = await service.close_rcv_and_play(round_record.id)

    assert final_round.status == RoundStatus.PLAYING
    assert final_round.winner_title == "Alien"
    assert downloader.requested[0].title == "Alien"
    assert player.loaded == ["C:/movies/winner.mkv"]


@pytest.mark.asyncio
async def test_rejects_second_active_suggestion_from_same_viewer(workspace_tmp: Path) -> None:
    service, *_ = make_service(workspace_tmp)

    await service.start_round()
    first = await service.submit_suggestion(platform="discord", user_id="1", display_name="One", raw_text="Shame 2012")
    second = await service.submit_suggestion(platform="discord", user_id="1", display_name="One", raw_text="Heat 1995")

    assert first.accepted
    assert not second.accepted
    assert "already have one active suggestion" in second.message


@pytest.mark.asyncio
async def test_admin_bypass_allows_multiple_active_suggestions_from_same_viewer(workspace_tmp: Path) -> None:
    service, *_ = make_service(workspace_tmp)

    await service.start_round()
    first = await service.submit_suggestion(platform="discord", user_id="1", display_name="One", raw_text="Shame 2012")
    second = await service.submit_suggestion(
        platform="discord",
        user_id="1",
        display_name="One",
        raw_text="Heat 1995",
        bypass_suggestion_limit=True,
    )

    assert first.accepted
    assert second.accepted


@pytest.mark.asyncio
async def test_reroll_replaces_open_approval_poll(workspace_tmp: Path) -> None:
    service, voting, *_ = make_service(workspace_tmp)

    round_record = await service.start_round()
    await service.submit_suggestion(platform="discord", user_id="1", display_name="One", raw_text="Shame 2012")
    await service.submit_suggestion(platform="discord", user_id="2", display_name="Two", raw_text="Heat 1995")
    after_collection = await service.close_collection(round_record.id)
    old_poll_id = after_collection.approval_poll_id

    rerolled = await service.reroll(round_record.id)

    assert rerolled.status == RoundStatus.APPROVAL_OPEN
    assert rerolled.approval_poll_id != old_poll_id
    assert not voting.snapshot(old_poll_id).is_open


def _candidate_id(candidates, title: str) -> str:
    return next(candidate.id for candidate in candidates if candidate.title == title)
