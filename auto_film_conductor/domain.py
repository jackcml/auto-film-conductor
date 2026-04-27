from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResolvedMovie:
    title: str
    year: int | None
    tmdb_id: int | None = None
    radarr_id: int | None = None
    overview: str | None = None
    file_path: str | None = None

    @property
    def movie_key(self) -> str:
        year_part = self.year if self.year is not None else "unknown"
        if self.tmdb_id is not None:
            return f"tmdb:{self.tmdb_id}"
        return f"title:{self.title.casefold()}:{year_part}"


@dataclass(frozen=True)
class PollCandidate:
    id: str
    title: str
    year: int | None = None

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True)
class PollResult:
    poll_id: str
    winner_candidate_id: str | None
    ranked_candidate_ids: list[str]
    scores: dict[str, int]


@dataclass(frozen=True)
class DownloadProgress:
    movie_id: int
    title: str | None
    status: str | None
    tracked_download_status: str | None
    tracked_download_state: str | None
    percent: float | None
    estimated_completion_time: str | None
    time_left: str | None


class MovieResolver(Protocol):
    async def resolve(self, query: str) -> ResolvedMovie | None:
        ...


class Downloader(Protocol):
    async def request_and_wait(self, movie: ResolvedMovie) -> ResolvedMovie:
        ...


class PlayerController(Protocol):
    async def load(self, file_path: str) -> None:
        ...

    async def stop(self) -> None:
        ...


class VotingProvider(Protocol):
    async def create_poll(
        self,
        *,
        kind: str,
        title: str,
        candidates: list[PollCandidate],
        duration_seconds: int,
    ) -> str:
        ...

    async def close_poll(self, poll_id: str) -> None:
        ...

    async def get_results(self, poll_id: str) -> PollResult:
        ...
