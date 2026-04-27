from __future__ import annotations

import httpx
import pytest

from auto_film_conductor.adapters.radarr import RadarrClient
from auto_film_conductor.domain import ResolvedMovie
from auto_film_conductor.path_mapping import parse_path_mappings


@pytest.mark.asyncio
async def test_request_and_wait_returns_playback_mapped_file_path() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/api/v3/command":
            return httpx.Response(200, json={"id": 1})
        if request.method == "GET" and request.url.path == "/api/v3/movie/10":
            return httpx.Response(200, json={"movieFile": {"path": "/movies/Alien (1979)/Alien.mkv"}})
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = RadarrClient(
            base_url="http://radarr.local",
            api_key="secret",
            root_folder_path="/movies",
            quality_profile_id=1,
            client=http_client,
            playback_path_maps=parse_path_mappings(r"/movies=D:\Media\Movies"),
        )

        imported = await client.request_and_wait(ResolvedMovie("Alien", 1979, tmdb_id=348, radarr_id=10))

    assert imported.file_path == r"D:\Media\Movies\Alien (1979)\Alien.mkv"
    assert [request.url.path for request in requests] == ["/api/v3/command", "/api/v3/movie/10"]


@pytest.mark.asyncio
async def test_download_progress_reads_queue_percent_and_eta() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/v3/queue":
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "movieId": 10,
                            "title": "Alien",
                            "status": "downloading",
                            "trackedDownloadStatus": "ok",
                            "trackedDownloadState": "downloading",
                            "size": 1000,
                            "sizeleft": 250,
                            "estimatedCompletionTime": "2026-04-27T03:30:00Z",
                            "timeleft": "00:04:12",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = RadarrClient(
            base_url="http://radarr.local",
            api_key="secret",
            root_folder_path="/movies",
            quality_profile_id=1,
            client=http_client,
        )

        progress = await client.download_progress(10)

    assert progress is not None
    assert progress.movie_id == 10
    assert progress.title == "Alien"
    assert progress.percent == 75.0
    assert progress.time_left == "00:04:12"
    assert requests[0].url.path == "/api/v3/queue"
    assert requests[0].url.params["movieIds"] == "10"


@pytest.mark.asyncio
async def test_download_progress_returns_none_when_movie_is_not_queued() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v3/queue":
            return httpx.Response(200, json={"records": [{"movieId": 20, "title": "Heat"}]})
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = RadarrClient(
            base_url="http://radarr.local",
            api_key="secret",
            root_folder_path="/movies",
            quality_profile_id=1,
            client=http_client,
        )

        progress = await client.download_progress(10)

    assert progress is None


@pytest.mark.asyncio
async def test_request_wraps_connect_errors_with_clear_radarr_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = RadarrClient(
            base_url="http://radarr.local",
            api_key="secret",
            root_folder_path="/movies",
            quality_profile_id=1,
            client=http_client,
        )

        with pytest.raises(RuntimeError, match="Radarr is unreachable at http://radarr.local"):
            await client.resolve("Alien 1979")
