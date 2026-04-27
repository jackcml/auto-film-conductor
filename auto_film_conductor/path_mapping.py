from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PathMapping:
    source_prefix: str
    playback_prefix: str


def parse_path_mappings(raw: str | None) -> tuple[PathMapping, ...]:
    if not raw:
        return ()

    mappings: list[PathMapping] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        source, separator, playback = entry.partition("=")
        if not separator:
            raise ValueError(f"Invalid playback path map {entry!r}; expected source=playback")
        source = _trim_prefix(source.strip())
        playback = _trim_prefix(playback.strip())
        if not source or not playback:
            raise ValueError(f"Invalid playback path map {entry!r}; source and playback must both be set")
        mappings.append(PathMapping(source, playback))

    return tuple(sorted(mappings, key=lambda mapping: len(mapping.source_prefix), reverse=True))


def map_playback_path(file_path: str, mappings: tuple[PathMapping, ...]) -> str:
    for mapping in mappings:
        if not _has_prefix(file_path, mapping.source_prefix):
            continue

        suffix = file_path[len(mapping.source_prefix) :]
        suffix = suffix.lstrip("/\\")
        if not suffix:
            return mapping.playback_prefix

        separator = "\\" if "\\" in mapping.playback_prefix else "/"
        suffix = suffix.replace("/", separator).replace("\\", separator)
        if mapping.playback_prefix.endswith(("/", "\\")):
            return f"{mapping.playback_prefix}{suffix}"
        return f"{mapping.playback_prefix}{separator}{suffix}"

    return file_path


def _has_prefix(file_path: str, source_prefix: str) -> bool:
    if source_prefix in {"/", "\\"}:
        return file_path.startswith(source_prefix)
    if file_path == source_prefix:
        return True
    if not file_path.startswith(source_prefix):
        return False
    return file_path[len(source_prefix)] in {"/", "\\"}


def _trim_prefix(value: str) -> str:
    if value in {"/", "\\"}:
        return value
    if len(value) == 3 and value[1] == ":" and value[2] in {"/", "\\"}:
        return value
    return value.rstrip("/\\")
