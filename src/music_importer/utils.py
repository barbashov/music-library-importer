from __future__ import annotations

import re
import shutil
from pathlib import Path

from music_importer.config import ALL_AUDIO_EXTS, UNSAFE_FILENAME_CHARS

_DISC_PATTERN = re.compile(r"^(cd|disc|disk|d)\s*(\d+)$", re.IGNORECASE)
_GENERIC_DIR_NAMES = {
    "",
    "albums",
    "downloads",
    "input",
    "library",
    "lossless",
    "music",
    "output",
    "temp",
    "tmp",
}
_PLACEHOLDER_VALUES = {
    "",
    "<unknown>",
    "n/a",
    "none",
    "unknown",
    "unknown album",
    "unknown artist",
}


def sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores.

    Strips control characters, leading/trailing dots and spaces.
    """
    for ch in UNSAFE_FILENAME_CHARS:
        name = name.replace(ch, "_")
    # Remove control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Strip leading/trailing dots and spaces
    name = name.strip(". ")
    return name or "Untitled"


def check_external_tools() -> list[str]:
    """Return list of missing required external tools."""
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def check_shnsplit_available() -> bool:
    return shutil.which("shnsplit") is not None


def check_cue_dependencies(input_dir: Path) -> list[str]:
    """Return missing external tools required for splitting CUE-based inputs.

    For CUE+FLAC workflows, both ``shnsplit`` and ``flac`` are required.
    """
    cue_files = find_cue_files(input_dir)
    if not cue_files:
        return []

    has_splittable_cue = False
    requires_flac_decoder = False
    for cue_file in cue_files:
        for ext in ALL_AUDIO_EXTS:
            candidate = cue_file.with_suffix(ext)
            if not candidate.exists():
                continue
            has_splittable_cue = True
            if candidate.suffix.lower() == ".flac":
                requires_flac_decoder = True
            break

    if not has_splittable_cue:
        return []

    missing: list[str] = []
    if shutil.which("shnsplit") is None:
        missing.append("shnsplit")
    if requires_flac_decoder and shutil.which("flac") is None:
        missing.append("flac")
    return missing


def normalize_metadata_value(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def is_placeholder_value(value: str | None) -> bool:
    normalized = normalize_metadata_value(value).lower()
    return normalized in _PLACEHOLDER_VALUES


def is_generic_dir_name(name: str | None) -> bool:
    normalized = normalize_metadata_value(name).lower()
    return normalized in _GENERIC_DIR_NAMES


def infer_artist_album(input_dir: Path) -> tuple[str, str]:
    """Infer artist and album from directory structure.

    Strategies:
    1. Parent/Child: Music/Artist/Album -> (Artist, Album)
    2. Dash split: "Artist - Album" folder -> (Artist, Album)
    3. Fallback: ("Unknown Artist", dir_name)
    """
    album_dir = input_dir.name
    artist_dir = input_dir.parent.name

    # If parent looks like a root dir, try splitting folder name
    if " - " in album_dir and is_generic_dir_name(artist_dir):
        parts = album_dir.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()

    if artist_dir and not is_generic_dir_name(artist_dir):
        return artist_dir, album_dir

    return "Unknown Artist", album_dir


def find_audio_files(directory: Path) -> list[Path]:
    """Find audio files in a directory (non-recursive), sorted by name."""
    return sorted(
        f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in ALL_AUDIO_EXTS
    )


def find_cue_files(directory: Path) -> list[Path]:
    """Find CUE files with case-insensitive deduplication."""
    seen: set[str] = set()
    result: list[Path] = []
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() == ".cue":
            key = f.name.lower()
            if key not in seen:
                seen.add(key)
                result.append(f)
    return result


def detect_disc_subdirs(input_dir: Path) -> list[tuple[int, Path]] | None:
    """Detect disc-numbered subdirectories.

    Returns sorted list of (disc_number, path) if disc pattern found, else None.
    Only used as a last-resort hint — MusicBrainz and source tags take priority.
    """
    candidates: list[tuple[int, Path]] = []
    for entry in sorted(input_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = _DISC_PATTERN.match(entry.name)
        if m:
            disc_num = int(m.group(2))
            if find_audio_files(entry):
                candidates.append((disc_num, entry))

    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[0])


def has_audio_subdirs(input_dir: Path) -> list[Path]:
    """Return subdirectories that contain audio files."""
    result = []
    for entry in sorted(input_dir.iterdir()):
        if entry.is_dir() and find_audio_files(entry):
            result.append(entry)
    return result
