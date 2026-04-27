from __future__ import annotations

import asyncio
from typing import Any

import httpx

from auto_film_conductor.domain import ResolvedMovie
from auto_film_conductor.path_mapping import PathMapping, map_playback_path


class RadarrClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        root_folder_path: str,
        quality_profile_id: int,
        client: httpx.AsyncClient | None = None,
        import_timeout_seconds: int = 3600,
        poll_interval_seconds: int = 20,
        playback_path_maps: tuple[PathMapping, ...] = (),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.root_folder_path = root_folder_path
        self.quality_profile_id = quality_profile_id
        self._client = client
        self.import_timeout_seconds = import_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.playback_path_maps = playback_path_maps

    async def resolve(self, query: str) -> ResolvedMovie | None:
        data = await self._request("GET", "/api/v3/movie/lookup", params={"term": query})
        if not data:
            return None
        movie = data[0]
        return _movie_from_radarr(movie, self.playback_path_maps)

    async def request_and_wait(self, movie: ResolvedMovie) -> ResolvedMovie:
        radarr_id = movie.radarr_id
        if radarr_id is None:
            radarr_id = await self._add_movie(movie)
        await self._request("POST", "/api/v3/command", json={"name": "MoviesSearch", "movieIds": [radarr_id]})

        deadline = asyncio.get_running_loop().time() + self.import_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            imported = await self._movie_by_id(radarr_id)
            file_path = imported.get("movieFile", {}).get("path")
            if file_path:
                playback_path = map_playback_path(file_path, self.playback_path_maps)
                return ResolvedMovie(
                    title=movie.title,
                    year=movie.year,
                    tmdb_id=movie.tmdb_id,
                    radarr_id=radarr_id,
                    overview=movie.overview,
                    file_path=playback_path,
                )
            await asyncio.sleep(self.poll_interval_seconds)

        raise TimeoutError(f"Timed out waiting for Radarr import: {movie.title}")

    async def _movie_by_id(self, radarr_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/v3/movie/{radarr_id}")

    async def _add_movie(self, movie: ResolvedMovie) -> int:
        if movie.tmdb_id is None:
            raise ValueError("Radarr add requires a TMDB ID")
        payload = {
            "title": movie.title,
            "tmdbId": movie.tmdb_id,
            "year": movie.year,
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder_path,
            "monitored": True,
            "addOptions": {"searchForMovie": True},
        }
        data = await self._request("POST", "/api/v3/movie", json=payload)
        return int(data["id"])

    async def _request(self, method: str, path: str, **kwargs):
        if not self.base_url or not self.api_key:
            raise RuntimeError("Radarr is not configured")
        headers = kwargs.pop("headers", {})
        headers["X-Api-Key"] = self.api_key
        if self._client is not None:
            response = await self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()


def _movie_from_radarr(movie: dict[str, Any], playback_path_maps: tuple[PathMapping, ...] = ()) -> ResolvedMovie:
    file_path = movie.get("movieFile", {}).get("path")
    return ResolvedMovie(
        title=movie.get("title", "Unknown title"),
        year=movie.get("year"),
        tmdb_id=movie.get("tmdbId"),
        radarr_id=movie.get("id"),
        overview=movie.get("overview"),
        file_path=map_playback_path(file_path, playback_path_maps) if file_path else None,
    )
