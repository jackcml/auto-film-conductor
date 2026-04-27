from __future__ import annotations

from pathlib import Path

from auto_film_conductor.config import Settings


def test_settings_loads_dotenv_file(workspace_tmp: Path, monkeypatch) -> None:
    monkeypatch.chdir(workspace_tmp)
    Path(".env").write_text(
        "\n".join(
            [
                "AFC_SAMPLE_SIZE=9",
                "AFC_DISCORD_CHANNEL_ID=12345",
                "AFC_RADARR_QUALITY_PROFILE_ID=7",
                r"AFC_PLAYBACK_PATH_MAPS=/movies=D:\Media\Movies",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env()

    assert settings.sample_size == 9
    assert settings.discord_channel_id == 12345
    assert settings.radarr_quality_profile_id == 7
    assert len(settings.playback_path_maps) == 1
    assert settings.playback_path_maps[0].source_prefix == "/movies"
    assert settings.playback_path_maps[0].playback_prefix == r"D:\Media\Movies"


def test_real_environment_overrides_dotenv_file(workspace_tmp: Path, monkeypatch) -> None:
    monkeypatch.chdir(workspace_tmp)
    Path(".env").write_text("AFC_SAMPLE_SIZE=9\n", encoding="utf-8")
    monkeypatch.setenv("AFC_SAMPLE_SIZE", "12")

    settings = Settings.from_env()

    assert settings.sample_size == 12
