from __future__ import annotations

import asyncio
from contextlib import suppress
import re

import discord
from discord import app_commands

from auto_film_conductor.services.conductor import ConductorService


MENTION_RE = re.compile(r"^<@!?\d+>\s*(?P<query>.+)$")


class DiscordConductorBot(discord.Client):
    def __init__(
        self,
        *,
        conductor: ConductorService,
        channel_id: int | None,
        admin_role_id: int | None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.conductor = conductor
        self.channel_id = channel_id
        self.admin_role_id = admin_role_id
        self.tree = app_commands.CommandTree(self)
        self._expiry_task: asyncio.Task[None] | None = None
        self._register_commands()

    async def setup_hook(self) -> None:
        await self.tree.sync()
        self._expiry_task = asyncio.create_task(self.conductor.run_expiry_monitor())

    async def close(self) -> None:
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._expiry_task
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self.channel_id is not None and message.channel.id != self.channel_id:
            return
        if self.user is None or self.user not in message.mentions:
            return
        match = MENTION_RE.match(message.content.strip())
        query = match.group("query").strip() if match else message.content.replace(self.user.mention, "", 1).strip()
        try:
            result = await self.conductor.submit_suggestion(
                platform="discord",
                user_id=str(message.author.id),
                display_name=message.author.display_name,
                raw_text=query,
                bypass_suggestion_limit=self._has_admin_role(message.author),
            )
            reply = result.message
        except RuntimeError:
            reply = "Movie lookup is unavailable right now. Check Radarr and try again."
        except ValueError as exc:
            reply = str(exc)
        await message.reply(reply, mention_author=False)

    def _register_commands(self) -> None:
        group = app_commands.Group(name="conductor", description="Control the automated movie-night conductor.")

        @group.command(name="start", description="Start a movie-night suggestion round.")
        async def start(interaction: discord.Interaction):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.start_round()
            await interaction.response.send_message(f"Started round {round_record.id}.", ephemeral=True)

        @group.command(name="status", description="Show the active conductor round.")
        async def status(interaction: discord.Interaction):
            round_record = await self.conductor.current_round()
            if round_record is None:
                await interaction.response.send_message("No active round.", ephemeral=True)
                return
            await interaction.response.send_message(f"Round {round_record.id}: {round_record.status}.", ephemeral=True)

        @group.command(name="pause", description="Pause a conductor round.")
        async def pause(interaction: discord.Interaction, round_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.pause(round_id)
            await interaction.response.send_message(f"Paused round {round_record.id}.", ephemeral=True)

        @group.command(name="resume", description="Resume a paused conductor round.")
        async def resume(interaction: discord.Interaction, round_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.resume(round_id)
            await interaction.response.send_message(f"Resumed round {round_record.id}: {round_record.status}.", ephemeral=True)

        @group.command(name="cancel", description="Cancel a conductor round.")
        async def cancel(interaction: discord.Interaction, round_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.cancel(round_id)
            await interaction.response.send_message(f"Cancelled round {round_record.id}.", ephemeral=True)

        @group.command(name="force_close", description="Advance the current round by force-closing its active phase.")
        async def force_close(interaction: discord.Interaction, round_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            active = await self.conductor.current_round()
            if active is None or active.id != round_id:
                await interaction.response.send_message("That round is not active.", ephemeral=True)
                return
            if active.status == "collecting":
                round_record = await self.conductor.close_collection(round_id)
            elif active.status == "approval_open":
                round_record = await self.conductor.close_approval(round_id)
            elif active.status == "rcv_open":
                await interaction.response.defer(ephemeral=True)
                round_record = await self.conductor.close_rcv_and_play(round_id)
                await interaction.followup.send(f"Closed runoff and started playback for {round_record.winner_title}.", ephemeral=True)
                return
            else:
                await interaction.response.send_message(f"Cannot force-close state {active.status}.", ephemeral=True)
                return
            await interaction.response.send_message(f"Round {round_record.id}: {round_record.status}.", ephemeral=True)

        @group.command(name="reroll", description="Reroll the approval poll sample.")
        async def reroll(interaction: discord.Interaction, round_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.reroll(round_id)
            await interaction.response.send_message(f"Rerolled approval poll for round {round_record.id}.", ephemeral=True)

        @group.command(name="override", description="Override the selected winner.")
        async def override(interaction: discord.Interaction, round_id: int, suggestion_id: int):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            round_record = await self.conductor.override_winner(round_id, suggestion_id)
            await interaction.response.send_message(f"Winner is now {round_record.winner_title}.", ephemeral=True)

        @group.command(name="stop", description="Emergency stop local playback.")
        async def stop(interaction: discord.Interaction):
            if not self._is_admin(interaction):
                await interaction.response.send_message("You cannot control the conductor.", ephemeral=True)
                return
            await self.conductor.stop_playback()
            await interaction.response.send_message("Playback stop sent.", ephemeral=True)

        self.tree.add_command(group)

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return self._has_admin_role(interaction.user)

    def _has_admin_role(self, user: discord.User | discord.Member) -> bool:
        if self.admin_role_id is None:
            return True
        return isinstance(user, discord.Member) and any(role.id == self.admin_role_id for role in user.roles)
