from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from auto_film_conductor.app import create_app
from auto_film_conductor.config import Settings
from auto_film_conductor.domain import DownloadProgress
from auto_film_conductor.models import PollRecord, PollStatus, Round, RoundStatus, Suggestion, SuggestionStatus, utc_now


class FakeProgressProvider:
    async def download_progress(self, movie_id: int) -> DownloadProgress | None:
        return DownloadProgress(
            movie_id=movie_id,
            title="Alien",
            status="downloading",
            tracked_download_status="ok",
            tracked_download_state="downloading",
            percent=72.5,
            estimated_completion_time="2026-04-27T03:30:00Z",
            time_left="00:04:12",
        )


def make_client(workspace_tmp: Path) -> TestClient:
    app = create_app(Settings(database_url=f"sqlite:///{workspace_tmp / 'viewer.db'}"))
    return TestClient(app)


def test_viewer_serves_overlay_page(workspace_tmp: Path) -> None:
    with make_client(workspace_tmp) as client:
        response = client.get("/viewer")

    assert response.status_code == 200
    assert "Auto Film Conductor Viewer" in response.text
    assert "/viewer/state" in response.text


def test_viewer_state_returns_no_active_round(workspace_tmp: Path) -> None:
    with make_client(workspace_tmp) as client:
        response = client.get("/viewer/state")

    assert response.status_code == 200
    assert response.json()["round"] is None


def test_viewer_state_shows_accepted_suggestions_with_display_names_only(workspace_tmp: Path) -> None:
    with make_client(workspace_tmp) as client:
        with client.app.state.afc.session_factory() as session:
            round_record = Round(
                status=RoundStatus.COLLECTING,
                collection_closes_at=utc_now() + timedelta(seconds=90),
                sample_size=15,
                runoff_size=5,
                approval_poll_seconds=300,
                rcv_poll_seconds=300,
            )
            session.add(round_record)
            session.commit()
            session.refresh(round_record)
            session.add(
                Suggestion(
                    round_id=round_record.id,
                    platform="discord",
                    user_id="secret-user-id",
                    display_name="Mina",
                    raw_text="raw accepted text",
                    status=SuggestionStatus.ACCEPTED,
                    title="Alien",
                    year=1979,
                    movie_key="tmdb:348",
                    tmdb_id=348,
                    radarr_id=10,
                )
            )
            session.add(
                Suggestion(
                    round_id=round_record.id,
                    platform="discord",
                    user_id="rejected-user-id",
                    display_name="Nope",
                    raw_text="Private rejected query",
                    status=SuggestionStatus.REJECTED,
                    rejection_reason="No matching movie found.",
                )
            )
            session.commit()

        response = client.get("/viewer/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["round"]["phase"] == "Suggestions open"
    assert payload["round"]["seconds_remaining"] > 0
    assert payload["round"]["suggestions"] == [
        {
            "id": 1,
            "title": "Alien",
            "year": 1979,
            "display_name": "Mina",
            "created_at": payload["round"]["suggestions"][0]["created_at"],
            "sampled": False,
        }
    ]
    assert "secret-user-id" not in response.text
    assert "rejected-user-id" not in response.text
    assert "Private rejected query" not in response.text
    assert "raw accepted text" not in response.text


def test_viewer_state_includes_active_poll_slate(workspace_tmp: Path) -> None:
    with make_client(workspace_tmp) as client:
        with client.app.state.afc.session_factory() as session:
            round_record = Round(
                status=RoundStatus.APPROVAL_OPEN,
                collection_closes_at=utc_now() - timedelta(seconds=1),
                sample_size=15,
                runoff_size=5,
                approval_poll_seconds=300,
                rcv_poll_seconds=300,
                approval_poll_id="poll-1",
            )
            session.add(round_record)
            session.commit()
            session.refresh(round_record)
            heat = Suggestion(
                round_id=round_record.id,
                platform="discord",
                user_id="1",
                display_name="One",
                raw_text="Heat 1995",
                status=SuggestionStatus.ACCEPTED,
                title="Heat",
                year=1995,
                sampled=True,
            )
            alien = Suggestion(
                round_id=round_record.id,
                platform="discord",
                user_id="2",
                display_name="Two",
                raw_text="Alien 1979",
                status=SuggestionStatus.ACCEPTED,
                title="Alien",
                year=1979,
                sampled=True,
            )
            session.add(heat)
            session.add(alien)
            session.commit()
            session.refresh(heat)
            session.refresh(alien)
            session.add(
                PollRecord(
                    round_id=round_record.id,
                    kind="approval",
                    external_id="poll-1",
                    status=PollStatus.OPEN,
                    candidate_suggestion_ids=f"[{alien.id},{heat.id}]",
                )
            )
            session.commit()

        response = client.get("/viewer/state")

    assert response.status_code == 200
    poll = response.json()["round"]["poll"]
    assert poll["kind"] == "approval"
    assert [candidate["title"] for candidate in poll["candidates"]] == ["Alien", "Heat"]
    assert response.json()["round"]["seconds_remaining"] > 0


def test_viewer_state_shows_download_progress_without_file_path(workspace_tmp: Path) -> None:
    with make_client(workspace_tmp) as client:
        client.app.state.afc.radarr = FakeProgressProvider()
        with client.app.state.afc.session_factory() as session:
            round_record = Round(
                status=RoundStatus.DOWNLOADING,
                collection_closes_at=utc_now() - timedelta(seconds=1),
                sample_size=15,
                runoff_size=5,
                approval_poll_seconds=300,
                rcv_poll_seconds=300,
                winner_title="Alien",
                winner_file_path=r"C:\Movies\Alien.mkv",
            )
            session.add(round_record)
            session.commit()
            session.refresh(round_record)
            winner = Suggestion(
                round_id=round_record.id,
                platform="discord",
                user_id="1",
                display_name="One",
                raw_text="Alien 1979",
                status=SuggestionStatus.ACCEPTED,
                title="Alien",
                year=1979,
                radarr_id=10,
            )
            session.add(winner)
            session.commit()
            session.refresh(winner)
            round_record.winner_suggestion_id = winner.id
            session.add(round_record)
            session.commit()

        response = client.get("/viewer/state")

    assert response.status_code == 200
    download = response.json()["round"]["download"]
    assert download["title"] == "Alien"
    assert download["percent"] == 72.5
    assert download["message"] == "Downloading via Radarr"
    assert r"C:\Movies\Alien.mkv" not in response.text
