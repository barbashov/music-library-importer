from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrackInfo:
    title: str
    artist: str
    track_number: int
    total_tracks: int
    disc_number: int
    total_discs: int


@dataclass
class ReleaseInfo:
    release_id: str
    release_group_id: str | None
    title: str
    artist: str
    date: str
    year: str
    genre: str
    tracks: dict[int, TrackInfo] = field(default_factory=dict)
    cover_data: bytes | None = None


@dataclass
class ConversionTask:
    source: Path
    destination: Path
    codec: str  # "alac" or "aac"
    tags: dict[str, Any] = field(default_factory=dict)
    is_temporary: bool = False
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class ConversionPlan:
    input_dir: Path
    output_dir: Path
    artist: str
    album: str
    year: str
    genre: str
    tasks: list[ConversionTask] = field(default_factory=list)
    cover_data: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    metadata_source: str = ""  # "musicbrainz", "source_tags", "filename"
