from __future__ import annotations

from auto_film_conductor.adapters.discord_bot import DiscordConductorBot
from auto_film_conductor.app import AppState
from auto_film_conductor.config import Settings
from auto_film_conductor.storage import init_db


def run_discord() -> None:
    settings = Settings.from_env()
    if not settings.discord_token:
        raise SystemExit("AFC_DISCORD_TOKEN is required")
    state = AppState(settings)
    init_db(state.engine)
    bot = DiscordConductorBot(
        conductor=state.conductor,
        channel_id=settings.discord_channel_id,
        admin_role_id=settings.discord_admin_role_id,
    )
    bot.run(settings.discord_token)
