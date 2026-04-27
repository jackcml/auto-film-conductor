from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from auto_film_conductor.path_mapping import PathMapping, parse_path_mappings


def _env(name: str, dotenv_env: Mapping[str, str | None]) -> str | None:
    if name in os.environ:
        return os.environ[name]
    return dotenv_env.get(name)


def _int_env(name: str, default: int, dotenv_env: Mapping[str, str | None]) -> int:
    raw = _env(name, dotenv_env)
    if raw is None or raw == "":
        return default
    return int(raw)


def _optional_int_env(name: str, dotenv_env: Mapping[str, str | None]) -> int | None:
    raw = _env(name, dotenv_env)
    if raw is None or raw == "":
        return None
    return int(raw)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./auto-film-conductor.db"
    suggestion_window_seconds: int = 300
    sample_size: int = 15
    runoff_size: int = 5
    approval_poll_seconds: int = 300
    rcv_poll_seconds: int = 300
    discord_token: str = ""
    discord_guild_id: int | None = None
    discord_channel_id: int | None = None
    discord_admin_role_id: int | None = None
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder_path: str = ""
    radarr_quality_profile_id: int = 1
    playback_path_maps: tuple[PathMapping, ...] = ()
    mpv_ipc_path: str = r"\\.\pipe\mpv-pipe"

    @classmethod
    def from_env(cls) -> "Settings":
        dotenv_env = dotenv_values(Path.cwd() / ".env")
        return cls(
            database_url=_env("AFC_DATABASE_URL", dotenv_env) or cls.database_url,
            suggestion_window_seconds=_int_env("AFC_SUGGESTION_WINDOW_SECONDS", cls.suggestion_window_seconds, dotenv_env),
            sample_size=_int_env("AFC_SAMPLE_SIZE", cls.sample_size, dotenv_env),
            runoff_size=_int_env("AFC_RUNOFF_SIZE", cls.runoff_size, dotenv_env),
            approval_poll_seconds=_int_env("AFC_APPROVAL_POLL_SECONDS", cls.approval_poll_seconds, dotenv_env),
            rcv_poll_seconds=_int_env("AFC_RCV_POLL_SECONDS", cls.rcv_poll_seconds, dotenv_env),
            discord_token=_env("AFC_DISCORD_TOKEN", dotenv_env) or "",
            discord_guild_id=_optional_int_env("AFC_DISCORD_GUILD_ID", dotenv_env),
            discord_channel_id=_optional_int_env("AFC_DISCORD_CHANNEL_ID", dotenv_env),
            discord_admin_role_id=_optional_int_env("AFC_DISCORD_ADMIN_ROLE_ID", dotenv_env),
            radarr_url=_env("AFC_RADARR_URL", dotenv_env) or "",
            radarr_api_key=_env("AFC_RADARR_API_KEY", dotenv_env) or "",
            radarr_root_folder_path=_env("AFC_RADARR_ROOT_FOLDER_PATH", dotenv_env) or "",
            radarr_quality_profile_id=_int_env("AFC_RADARR_QUALITY_PROFILE_ID", cls.radarr_quality_profile_id, dotenv_env),
            playback_path_maps=parse_path_mappings(_env("AFC_PLAYBACK_PATH_MAPS", dotenv_env)),
            mpv_ipc_path=_env("AFC_MPV_IPC_PATH", dotenv_env) or cls.mpv_ipc_path,
        )
