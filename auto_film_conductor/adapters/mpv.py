from __future__ import annotations

import asyncio
import json
import os


class MpvIpcController:
    def __init__(self, ipc_path: str) -> None:
        self.ipc_path = ipc_path

    async def load(self, file_path: str) -> None:
        await self._command(["loadfile", file_path, "replace"])

    async def stop(self) -> None:
        await self._command(["stop"])

    async def _command(self, command: list[str]) -> None:
        payload = json.dumps({"command": command}) + "\n"
        if os.name == "nt":
            await asyncio.to_thread(_write_windows_pipe, self.ipc_path, payload)
            return

        reader, writer = await asyncio.open_unix_connection(self.ipc_path)
        try:
            writer.write(payload.encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def _write_windows_pipe(path: str, payload: str) -> None:
    with open(path, "w", encoding="utf-8") as pipe:
        pipe.write(payload)
