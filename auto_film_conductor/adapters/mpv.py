from __future__ import annotations

import asyncio
import json
import os


class MpvIpcController:
    def __init__(self, ipc_path: str, *, platform_name: str | None = None) -> None:
        self.ipc_path = ipc_path
        self.platform_name = platform_name or os.name

    async def load(self, file_path: str) -> None:
        await self._command(["loadfile", file_path, "replace"])

    async def stop(self) -> None:
        await self._command(["stop"])

    async def _command(self, command: list[str]) -> None:
        payload = json.dumps({"command": command}) + "\n"
        if self.platform_name == "nt":
            await asyncio.to_thread(_write_windows_pipe, self.ipc_path, payload)
            return

        await _write_unix_socket(self.ipc_path, payload)


async def _write_unix_socket(path: str, payload: str) -> None:
    open_unix_connection = getattr(asyncio, "open_unix_connection", None)
    if open_unix_connection is None:
        raise RuntimeError("mpv Unix socket IPC is not available on this platform")

    reader, writer = await open_unix_connection(path)
    try:
        writer.write(payload.encode("utf-8"))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _write_windows_pipe(path: str, payload: str) -> None:
    with open(path, "w", encoding="utf-8") as pipe:
        pipe.write(payload)
