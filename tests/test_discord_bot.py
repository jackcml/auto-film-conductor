from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from auto_film_conductor.adapters.discord_bot import DiscordConductorBot


class FailingConductor:
    async def submit_suggestion(self, **kwargs):
        raise RuntimeError("Radarr is unreachable at http://radarr.local")


class FakeMessage:
    def __init__(self, bot_user) -> None:
        self.author = SimpleNamespace(bot=False, id=123, display_name="Mina")
        self.channel = SimpleNamespace(id=456)
        self.mentions = [bot_user]
        self.content = f"{bot_user.mention} Alien 1979"
        self.replies: list[str] = []

    async def reply(self, content: str, *, mention_author: bool) -> None:
        self.replies.append(content)
        assert mention_author is False


@pytest.mark.asyncio
async def test_discord_message_replies_when_lookup_backend_is_unavailable() -> None:
    bot_user = SimpleNamespace(mention="<@999>")
    bot = object.__new__(DiscordConductorBot)
    bot._connection = cast(Any, SimpleNamespace(user=bot_user))
    bot.conductor = cast(Any, FailingConductor())
    bot.channel_id = None
    bot.admin_role_id = None
    message = FakeMessage(bot_user)

    await bot.on_message(cast(Any, message))

    assert message.replies == ["Movie lookup is unavailable right now. Check Radarr and try again."]
