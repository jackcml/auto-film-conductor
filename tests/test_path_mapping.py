from __future__ import annotations

import pytest

from auto_film_conductor.path_mapping import map_playback_path, parse_path_mappings


def test_maps_docker_path_to_windows_playback_path() -> None:
    mappings = parse_path_mappings(r"/movies=D:\Media\Movies")

    mapped = map_playback_path("/movies/Alien (1979)/Alien.mkv", mappings)

    assert mapped == r"D:\Media\Movies\Alien (1979)\Alien.mkv"


def test_maps_docker_path_to_container_playback_path() -> None:
    mappings = parse_path_mappings("/radarr-movies=/media/movies")

    mapped = map_playback_path("/radarr-movies/Heat (1995)/Heat.mkv", mappings)

    assert mapped == "/media/movies/Heat (1995)/Heat.mkv"


def test_uses_longest_matching_prefix() -> None:
    mappings = parse_path_mappings("/movies=/media/movies;/movies/uhd=/media/uhd")

    mapped = map_playback_path("/movies/uhd/Dune (2021)/Dune.mkv", mappings)

    assert mapped == "/media/uhd/Dune (2021)/Dune.mkv"


def test_does_not_match_partial_path_segment() -> None:
    mappings = parse_path_mappings("/movies=/media/movies")

    mapped = map_playback_path("/movies-extra/Alien.mkv", mappings)

    assert mapped == "/movies-extra/Alien.mkv"


def test_preserves_windows_drive_root_mapping() -> None:
    mappings = parse_path_mappings("/movies=D:\\")

    mapped = map_playback_path("/movies/Alien.mkv", mappings)

    assert mapped == "D:\\Alien.mkv"


def test_rejects_invalid_mapping() -> None:
    with pytest.raises(ValueError, match="expected source=playback"):
        parse_path_mappings("/movies")
