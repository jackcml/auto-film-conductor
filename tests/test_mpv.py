from __future__ import annotations

import asyncio

import pytest

from auto_film_conductor.adapters import mpv
from auto_film_conductor.adapters.mpv import MpvIpcController


@pytest.mark.asyncio
async def test_windows_pipe_path_does_not_touch_unix_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, str]] = []

    def fake_write_windows_pipe(path: str, payload: str) -> None:
        writes.append((path, payload))

    async def fake_open_unix_connection(path: str):
        raise AssertionError("Windows IPC must not open a Unix socket")

    monkeypatch.setattr(mpv, "_write_windows_pipe", fake_write_windows_pipe)
    monkeypatch.setattr(asyncio, "open_unix_connection", fake_open_unix_connection, raising=False)

    controller = MpvIpcController(r"\\.\pipe\mpv-pipe", platform_name="nt")
    await controller.load("C:/movies/Alien.mkv")

    assert writes == [
        (r"\\.\pipe\mpv-pipe", '{"command": ["loadfile", "C:/movies/Alien.mkv", "replace"]}\n')
    ]


@pytest.mark.asyncio
async def test_unix_socket_path_raises_clearly_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(asyncio, "open_unix_connection", raising=False)

    controller = MpvIpcController("/tmp/mpv.sock", platform_name="posix")

    with pytest.raises(RuntimeError, match="Unix socket IPC is not available"):
        await controller.stop()
