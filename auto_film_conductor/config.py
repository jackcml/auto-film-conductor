from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
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
    mpv_ipc_path: str = r"\\.\pipe\mpv-pipe"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("AFC_DATABASE_URL", cls.database_url),
            suggestion_window_seconds=_int_env("AFC_SUGGESTION_WINDOW_SECONDS", cls.suggestion_window_seconds),
            sample_size=_int_env("AFC_SAMPLE_SIZE", cls.sample_size),
            runoff_size=_int_env("AFC_RUNOFF_SIZE", cls.runoff_size),
            approval_poll_seconds=_int_env("AFC_APPROVAL_POLL_SECONDS", cls.approval_poll_seconds),
            rcv_poll_seconds=_int_env("AFC_RCV_POLL_SECONDS", cls.rcv_poll_seconds),
            discord_token=os.getenv("AFC_DISCORD_TOKEN", ""),
            discord_guild_id=_optional_int_env("AFC_DISCORD_GUILD_ID"),
            discord_channel_id=_optional_int_env("AFC_DISCORD_CHANNEL_ID"),
            discord_admin_role_id=_optional_int_env("AFC_DISCORD_ADMIN_ROLE_ID"),
            radarr_url=os.getenv("AFC_RADARR_URL", ""),
            radarr_api_key=os.getenv("AFC_RADARR_API_KEY", ""),
            radarr_root_folder_path=os.getenv("AFC_RADARR_ROOT_FOLDER_PATH", ""),
            radarr_quality_profile_id=_int_env("AFC_RADARR_QUALITY_PROFILE_ID", cls.radarr_quality_profile_id),
            mpv_ipc_path=os.getenv("AFC_MPV_IPC_PATH", cls.mpv_ipc_path),
        )


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return int(raw)
