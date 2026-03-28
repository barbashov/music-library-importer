from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.mp4 import MP4, MP4Cover


def write_tags(m4a_path: Path, tags: dict[str, Any], cover_data: bytes | None) -> None:
    """Write MP4/M4A tags using mutagen."""
    audio = MP4(str(m4a_path))

    if tags.get("title"):
        audio["\xa9nam"] = [tags["title"]]
    if tags.get("artist"):
        audio["\xa9ART"] = [tags["artist"]]
    if tags.get("album"):
        audio["\xa9alb"] = [tags["album"]]
    if tags.get("date"):
        audio["\xa9day"] = [tags["date"]]
    if tags.get("genre"):
        audio["\xa9gen"] = [tags["genre"]]
    if tags.get("albumartist"):
        audio["aART"] = [tags["albumartist"]]

    track, total_tracks = tags.get("track"), tags.get("total_tracks")
    if track:
        audio["trkn"] = [(track, total_tracks or 0)]

    disc, total_discs = tags.get("disc"), tags.get("total_discs")
    if disc:
        audio["disk"] = [(disc, total_discs or 0)]

    if cover_data:
        fmt = MP4Cover.FORMAT_PNG if cover_data[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover_data, imageformat=fmt)]

    audio.save()


def read_source_tags(file_path: Path) -> dict[str, Any]:
    """Read tags from a source audio file using mutagen.

    Returns a dict with keys: title, artist, album, albumartist, date, genre,
    track, total_tracks, disc, total_discs. Missing values are empty/zero.
    """
    result: dict[str, Any] = {
        "title": "",
        "artist": "",
        "album": "",
        "albumartist": "",
        "date": "",
        "genre": "",
        "track": 0,
        "total_tracks": 0,
        "disc": 0,
        "total_discs": 0,
    }

    try:
        audio = MutagenFile(str(file_path), easy=True)
    except Exception:
        return result

    if audio is None or audio.tags is None:
        return result

    tags = audio.tags

    def get_first(key: str) -> str:
        val = tags.get(key)
        if val and isinstance(val, list):
            return str(val[0])
        if val:
            return str(val)
        return ""

    result["title"] = get_first("title")
    result["artist"] = get_first("artist")
    result["album"] = get_first("album")
    result["albumartist"] = get_first("albumartist")
    result["date"] = get_first("date")
    result["genre"] = get_first("genre")

    # Track number: may be "3" or "3/12"
    track_str = get_first("tracknumber")
    if track_str:
        parts = track_str.split("/")
        with contextlib.suppress(ValueError):
            result["track"] = int(parts[0])
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                result["total_tracks"] = int(parts[1])

    disc_str = get_first("discnumber")
    if disc_str:
        parts = disc_str.split("/")
        with contextlib.suppress(ValueError):
            result["disc"] = int(parts[0])
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                result["total_discs"] = int(parts[1])

    return result
