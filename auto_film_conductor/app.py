from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from auto_film_conductor.adapters.mpv import MpvIpcController
from auto_film_conductor.adapters.radarr import RadarrClient
from auto_film_conductor.config import Settings
from auto_film_conductor.domain import PollCandidate
from auto_film_conductor.models import PollRecord, Round, Suggestion
from auto_film_conductor.services.conductor import ConductorService
from auto_film_conductor.storage import init_db, make_engine, session_dependency
from auto_film_conductor.viewer import ViewerStateResponse, build_viewer_state
from auto_film_conductor.voting import MockVotingProvider


class SuggestionRequest(BaseModel):
    platform: str = "api"
    user_id: str
    display_name: str
    raw_text: str


class SuggestionResponse(BaseModel):
    accepted: bool
    message: str
    suggestion_id: int | None = None


class OverrideRequest(BaseModel):
    suggestion_id: int


class MockPollCreateRequest(BaseModel):
    kind: str = Field(pattern="^(approval|rcv)$")
    title: str
    candidates: list[PollCandidate]
    duration_seconds: int = 300


class MockVoteRequest(BaseModel):
    voter_id: str
    approvals: list[str] = []
    ranking: list[str] = []


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.voting = MockVotingProvider()
        self.session_factory = lambda: Session(self.engine, expire_on_commit=False)
        self.radarr = RadarrClient(
            base_url=settings.radarr_url,
            api_key=settings.radarr_api_key,
            root_folder_path=settings.radarr_root_folder_path,
            quality_profile_id=settings.radarr_quality_profile_id,
            playback_path_maps=settings.playback_path_maps,
        )
        self.conductor = ConductorService(
            session_factory=self.session_factory,
            settings=settings,
            resolver=self.radarr,
            voting=self.voting,
            downloader=self.radarr,
            player=MpvIpcController(settings.mpv_ipc_path),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    state = AppState(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_db(state.engine)
        yield

    app = FastAPI(title="Auto Film Conductor", lifespan=lifespan)
    app.state.afc = state
    get_session = session_dependency(state.engine)

    def conductor() -> ConductorService:
        return state.conductor

    def voting() -> MockVotingProvider:
        return state.voting

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/viewer", response_class=HTMLResponse, include_in_schema=False)
    async def viewer():
        return HTMLResponse(_viewer_html())

    @app.get("/viewer/state", response_model=ViewerStateResponse)
    async def viewer_state(session: Session = Depends(get_session)):
        return await build_viewer_state(session, progress_provider=state.radarr)

    @app.get("/rounds/current", response_model=Round | None)
    async def current_round(service: ConductorService = Depends(conductor)):
        return await _call(service.current_round)

    @app.post("/rounds/start", response_model=Round)
    async def start_round(service: ConductorService = Depends(conductor)):
        return await _call(service.start_round)

    @app.post("/rounds/{round_id}/suggestions", response_model=SuggestionResponse)
    async def suggest(
        round_id: int,
        request: SuggestionRequest,
        service: ConductorService = Depends(conductor),
    ):
        active = await _call(service.current_round)
        if active is None or active.id != round_id:
            raise HTTPException(status_code=404, detail="Round is not active")
        result = await _call(
            service.submit_suggestion,
            platform=request.platform,
            user_id=request.user_id,
            display_name=request.display_name,
            raw_text=request.raw_text,
        )
        return SuggestionResponse(
            accepted=result.accepted,
            message=result.message,
            suggestion_id=result.suggestion.id if result.suggestion else None,
        )

    @app.post("/rounds/{round_id}/close-collection", response_model=Round)
    async def close_collection(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.close_collection, round_id)

    @app.post("/rounds/{round_id}/close-approval", response_model=Round)
    async def close_approval(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.close_approval, round_id)

    @app.post("/rounds/{round_id}/close-rcv", response_model=Round)
    async def close_rcv(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.close_rcv_and_play, round_id)

    @app.post("/rounds/{round_id}/pause", response_model=Round)
    async def pause(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.pause, round_id)

    @app.post("/rounds/{round_id}/resume", response_model=Round)
    async def resume(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.resume, round_id)

    @app.post("/rounds/{round_id}/cancel", response_model=Round)
    async def cancel(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.cancel, round_id)

    @app.post("/rounds/{round_id}/complete", response_model=Round)
    async def complete(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.complete, round_id)

    @app.post("/rounds/{round_id}/reroll", response_model=Round)
    async def reroll(round_id: int, service: ConductorService = Depends(conductor)):
        return await _call(service.reroll, round_id)

    @app.post("/rounds/{round_id}/force-close", response_model=Round)
    async def force_close(round_id: int, service: ConductorService = Depends(conductor)):
        active = await _call(service.current_round)
        if active is None or active.id != round_id:
            raise HTTPException(status_code=404, detail="Round is not active")
        if active.status == "collecting":
            return await _call(service.close_collection, round_id)
        if active.status == "approval_open":
            return await _call(service.close_approval, round_id)
        if active.status == "rcv_open":
            return await _call(service.close_rcv_and_play, round_id)
        raise HTTPException(status_code=409, detail=f"Cannot force-close round in state {active.status}")

    @app.post("/rounds/{round_id}/override", response_model=Round)
    async def override(round_id: int, request: OverrideRequest, service: ConductorService = Depends(conductor)):
        return await _call(service.override_winner, round_id, request.suggestion_id)

    @app.get("/rounds/{round_id}/suggestions", response_model=list[Suggestion])
    async def suggestions(round_id: int, session: Session = Depends(get_session)):
        return session.exec(select(Suggestion).where(Suggestion.round_id == round_id)).all()

    @app.get("/rounds/{round_id}/polls", response_model=list[PollRecord])
    async def polls(round_id: int, session: Session = Depends(get_session)):
        return session.exec(select(PollRecord).where(PollRecord.round_id == round_id)).all()

    @app.post("/playback/stop")
    async def stop_playback(service: ConductorService = Depends(conductor)):
        await _call(service.stop_playback)
        return {"ok": True}

    @app.post("/mock-polls")
    async def create_mock_poll(request: MockPollCreateRequest, provider: MockVotingProvider = Depends(voting)):
        poll_id = await _call(
            provider.create_poll,
            kind=request.kind,
            title=request.title,
            candidates=request.candidates,
            duration_seconds=request.duration_seconds,
        )
        return {"poll_id": poll_id}

    @app.post("/mock-polls/{poll_id}/votes")
    async def vote_mock_poll(
        poll_id: str,
        request: MockVoteRequest,
        provider: MockVotingProvider = Depends(voting),
    ):
        poll = provider.snapshot(poll_id)
        if poll.kind == "approval":
            await _call(provider.cast_approval_vote, poll_id, request.voter_id, request.approvals)
        else:
            await _call(provider.cast_rcv_vote, poll_id, request.voter_id, request.ranking)
        return {"ok": True}

    @app.post("/mock-polls/{poll_id}/close")
    async def close_mock_poll(poll_id: str, provider: MockVotingProvider = Depends(voting)):
        await _call(provider.close_poll, poll_id)
        return {"ok": True}

    @app.get("/mock-polls/{poll_id}/results")
    async def mock_poll_results(poll_id: str, provider: MockVotingProvider = Depends(voting)):
        return await _call(provider.get_results, poll_id)

    return app


async def _call(function, *args, **kwargs):
    try:
        return await function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _viewer_html() -> str:
    return files("auto_film_conductor").joinpath("static", "viewer.html").read_text(encoding="utf-8")


app = create_app()
