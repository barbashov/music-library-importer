from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from music_importer.config import ALL_AUDIO_EXTS, LOSSLESS_CODECS, LOSSLESS_EXTS
from music_importer.debug import truncate_text
from music_importer.models import ConversionPlan, ConversionTask, ReleaseInfo
from music_importer.tagger import read_source_tags, write_tags
from music_importer.utils import (
    detect_disc_subdirs,
    find_audio_files,
    find_cue_files,
    has_audio_subdirs,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


def _run_logged(
    cmd: list[str],
    *,
    timeout: int | None = None,
    capture_output: bool = False,
    text: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    debug_enabled = logger.isEnabledFor(logging.DEBUG)
    should_capture = capture_output or debug_enabled
    should_text = text or debug_enabled
    logger.debug("Running command: %s", shlex.join(cmd))
    result = subprocess.run(
        cmd,
        timeout=timeout,
        capture_output=should_capture,
        text=should_text,
        check=False,
    )
    if debug_enabled:
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        logger.debug(
            "Command finished rc=%s stdout=%s stderr=%s",
            result.returncode,
            truncate_text(stdout),
            truncate_text(stderr),
        )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def detect_codec(source: Path, force_format: str | None = None) -> str:
    """Determine output codec for a source file.

    Returns "alac" or "aac".
    """
    logger.debug("Detecting codec source=%s force_format=%s", source, force_format)
    if force_format in ("alac", "aac"):
        logger.debug("Codec forced to %s", force_format)
        return force_format

    # Extension-based fast path
    if source.suffix.lower() in LOSSLESS_EXTS:
        logger.debug("Codec detected via extension as alac")
        return "alac"

    # Use ffprobe for accurate detection
    try:
        result = _run_logged(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "a:0",
                str(source),
            ],
            text=True,
            timeout=10,
            capture_output=True,
            check=False,
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            codec_name = streams[0].get("codec_name", "")
            if codec_name in LOSSLESS_CODECS:
                logger.debug("Codec detected via ffprobe codec_name=%s -> alac", codec_name)
                return "alac"
            logger.debug("Codec detected via ffprobe codec_name=%s -> aac", codec_name)
            return "aac"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        logger.debug("ffprobe codec detection failed; falling back to aac", exc_info=True)
        pass

    # Fallback: assume lossy for unknown formats
    logger.debug("Codec fallback to aac")
    return "aac"


def ffmpeg_convert(src: Path, dst: Path, codec: str) -> None:
    """Convert audio file using ffmpeg."""
    logger.debug("Converting source=%s destination=%s codec=%s", src, dst, codec)
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:a",
    ]

    if codec == "alac":
        cmd += ["-c:a", "alac"]
    else:
        # AAC: quality encode without upsampling
        cmd += ["-c:a", "aac", "-b:a", "256k"]

    cmd += ["-y", str(dst)]
    _run_logged(cmd, text=True)


def split_cue(cue_file: Path, audio_file: Path, tmp_dir: Path) -> list[Path]:
    """Split a single-file + CUE into per-track WAVs."""
    logger.debug(
        "Splitting CUE cue_file=%s audio_file=%s tmp_dir=%s",
        cue_file,
        audio_file,
        tmp_dir,
    )
    _run_logged(
        [
            "shnsplit",
            "-d",
            str(tmp_dir),
            "-f",
            str(cue_file),
            "-o",
            "wav",
            str(audio_file),
            "-t",
            "%n",
        ],
        capture_output=True,
        text=True,
    )
    return sorted(tmp_dir.glob("*.wav"))


def parse_cue_titles(cue_file: Path) -> list[str]:
    """Extract track titles from a CUE sheet."""
    titles: list[str] = []
    with open(cue_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'\s+TITLE\s+"(.+)"', line)
            if m:
                titles.append(m.group(1))
    return titles


def parse_cue_track_count(cue_file: Path) -> int:
    """Count tracks in a CUE sheet by counting TRACK directives."""
    count = 0
    with open(cue_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            if re.match(r"\s*TRACK\s+\d+\s+AUDIO", line, re.IGNORECASE):
                count += 1
    return count


def _build_tags_dict(
    *,
    title: str,
    artist: str,
    album: str,
    albumartist: str,
    date: str,
    genre: str,
    track: int,
    total_tracks: int,
    disc: int,
    total_discs: int,
) -> dict[str, Any]:
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "albumartist": albumartist,
        "date": date,
        "genre": genre,
        "track": track,
        "total_tracks": total_tracks,
        "disc": disc,
        "total_discs": total_discs,
    }


def _collect_disc_files(input_dir: Path) -> list[tuple[int, list[Path]]]:
    """Collect audio files, detecting multi-disc layouts.

    Returns list of (disc_number, files) tuples.
    """
    # Check for audio files directly in input_dir
    direct_files = find_audio_files(input_dir)
    if direct_files:
        return [(1, direct_files)]

    # Check for subdirectories with audio
    audio_subdirs = has_audio_subdirs(input_dir)
    if not audio_subdirs:
        return []

    # Try disc-pattern detection from directory names
    disc_subdirs = detect_disc_subdirs(input_dir)
    if disc_subdirs:
        return [(disc_num, find_audio_files(path)) for disc_num, path in disc_subdirs]

    # For non-disc-named subdirs, read tags to determine if multi-disc
    # or treat each subdir as a separate disc in order
    albums_seen: set[str] = set()
    for subdir in audio_subdirs:
        files = find_audio_files(subdir)
        if files:
            tags = read_source_tags(files[0])
            album_key = f"{tags.get('album', '')}/{tags.get('albumartist', tags.get('artist', ''))}"
            albums_seen.add(album_key)

    if len(albums_seen) <= 1:
        # All subdirs have same album — multi-disc
        result = []
        for i, subdir in enumerate(audio_subdirs, 1):
            files = find_audio_files(subdir)
            if files:
                # Try to get disc number from tags
                tags = read_source_tags(files[0])
                disc_num = tags.get("disc", 0) or i
                result.append((disc_num, files))
        return sorted(result, key=lambda x: x[0])

    # Different albums in subdirs — not a multi-disc, just use first subdir
    return [(1, find_audio_files(audio_subdirs[0]))]


def build_plan(
    input_dir: Path,
    output_dir: Path,
    release_info: ReleaseInfo | None,
    artist: str,
    album: str,
    year: str,
    genre: str,
    force_format: str | None,
    dry_run: bool = False,
) -> ConversionPlan:
    """Build a complete conversion plan.

    In dry_run mode for CUE files, parses CUE textually instead of splitting.
    """
    plan = ConversionPlan(
        input_dir=input_dir,
        output_dir=output_dir,
        artist=artist,
        album=album,
        year=year,
        genre=genre,
        metadata_source="musicbrainz" if release_info else "filename",
    )

    track_map = release_info.tracks if release_info else {}

    # Check for CUE sheets
    cue_files = find_cue_files(input_dir)

    if cue_files:
        _build_cue_plan(plan, cue_files, track_map, force_format, dry_run)
    else:
        _build_track_plan(plan, track_map, force_format)

    return plan


def _build_cue_plan(
    plan: ConversionPlan,
    cue_files: list[Path],
    track_map: dict,
    force_format: str | None,
    dry_run: bool,
) -> None:
    global_track = 1
    total_cues = len(cue_files)

    for disc_idx, cue_file in enumerate(cue_files):
        # Find matching audio file
        audio_file = None
        for ext in ALL_AUDIO_EXTS:
            candidate = cue_file.with_suffix(ext)
            if candidate.exists():
                audio_file = candidate
                break

        if not audio_file:
            plan.warnings.append(f"No audio file for {cue_file.name}, skipping")
            continue

        codec = detect_codec(audio_file, force_format) if not dry_run else (force_format or "alac")
        cue_titles = parse_cue_titles(cue_file)

        if dry_run:
            # Parse CUE to estimate tracks without splitting
            track_count = parse_cue_track_count(cue_file)
            if track_count == 0:
                track_count = len(cue_titles)
            for i in range(track_count):
                mb_info = track_map.get(global_track)
                title = (
                    mb_info.title
                    if mb_info
                    else (cue_titles[i] if i < len(cue_titles) else f"Track {global_track:02d}")
                )
                track_n = mb_info.track_number if mb_info else (i + 1)
                total_t = mb_info.total_tracks if mb_info else track_count
                disc_n = mb_info.disc_number if mb_info else (disc_idx + 1)
                total_d = mb_info.total_discs if mb_info else total_cues

                out_name = f"{disc_n}-{track_n:02d} {sanitize_filename(title)}.m4a"
                plan.tasks.append(
                    ConversionTask(
                        source=audio_file,
                        destination=plan.output_dir / out_name,
                        codec=codec,
                        tags=_build_tags_dict(
                            title=title,
                            artist=mb_info.artist if mb_info else plan.artist,
                            album=plan.album,
                            albumartist=plan.artist,
                            date=plan.year,
                            genre=plan.genre,
                            track=track_n,
                            total_tracks=total_t,
                            disc=disc_n,
                            total_discs=total_d,
                        ),
                    )
                )
                global_track += 1
        else:
            # This branch is handled during execution, plan just stores CUE info
            track_count = parse_cue_track_count(cue_file)
            if track_count == 0:
                track_count = len(cue_titles)
            for i in range(track_count):
                mb_info = track_map.get(global_track)
                title = (
                    mb_info.title
                    if mb_info
                    else (cue_titles[i] if i < len(cue_titles) else f"Track {global_track:02d}")
                )
                track_n = mb_info.track_number if mb_info else (i + 1)
                total_t = mb_info.total_tracks if mb_info else track_count
                disc_n = mb_info.disc_number if mb_info else (disc_idx + 1)
                total_d = mb_info.total_discs if mb_info else total_cues

                out_name = f"{disc_n}-{track_n:02d} {sanitize_filename(title)}.m4a"
                plan.tasks.append(
                    ConversionTask(
                        source=audio_file,
                        destination=plan.output_dir / out_name,
                        codec=codec,
                        tags=_build_tags_dict(
                            title=title,
                            artist=mb_info.artist if mb_info else plan.artist,
                            album=plan.album,
                            albumartist=plan.artist,
                            date=plan.year,
                            genre=plan.genre,
                            track=track_n,
                            total_tracks=total_t,
                            disc=disc_n,
                            total_discs=total_d,
                        ),
                        is_temporary=True,
                    )
                )
                global_track += 1


def _build_track_plan(
    plan: ConversionPlan,
    track_map: dict,
    force_format: str | None,
) -> None:
    disc_files = _collect_disc_files(plan.input_dir)
    if not disc_files:
        plan.warnings.append("No audio files found in input directory")
        return

    total_discs = len(disc_files)
    global_track = 1

    for disc_num, files in disc_files:
        total_tracks = len(files)
        for i, src in enumerate(files):
            mb_info = track_map.get(global_track)

            if mb_info:
                title = mb_info.title
                artist = mb_info.artist
                track_n = mb_info.track_number
                total_t = mb_info.total_tracks
                disc_n = mb_info.disc_number
                total_d = mb_info.total_discs
            else:
                # Try source tags
                src_tags = read_source_tags(src)
                title = src_tags["title"] or src.stem
                artist = src_tags["artist"] or plan.artist
                track_n = src_tags["track"] or (i + 1)
                total_t = src_tags["total_tracks"] or total_tracks
                disc_n = src_tags["disc"] or disc_num
                total_d = src_tags["total_discs"] or total_discs

                if src_tags["title"]:
                    plan.metadata_source = plan.metadata_source or "source_tags"

            codec = detect_codec(src, force_format)

            out_name = f"{disc_n}-{track_n:02d} {sanitize_filename(title)}.m4a"
            plan.tasks.append(
                ConversionTask(
                    source=src,
                    destination=plan.output_dir / out_name,
                    codec=codec,
                    tags=_build_tags_dict(
                        title=title,
                        artist=artist,
                        album=plan.album,
                        albumartist=plan.artist,
                        date=plan.year,
                        genre=plan.genre,
                        track=track_n,
                        total_tracks=total_t,
                        disc=disc_n,
                        total_discs=total_d,
                    ),
                )
            )
            global_track += 1


def execute_plan(
    plan: ConversionPlan,
    on_progress: Any = None,
) -> None:
    """Execute a conversion plan.

    on_progress: callable(task_index, total, task) called after each conversion.
    """
    cue_files = find_cue_files(plan.input_dir)
    logger.debug(
        "Executing plan input_dir=%s output_dir=%s tasks=%d cue_files=%d",
        plan.input_dir,
        plan.output_dir,
        len(plan.tasks),
        len(cue_files),
    )

    if cue_files:
        _execute_cue_plan(plan, cue_files, on_progress)
    else:
        _execute_track_plan(plan, on_progress)


def _execute_cue_plan(
    plan: ConversionPlan,
    cue_files: list[Path],
    on_progress: Any,
) -> None:
    plan.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        task_idx = 0

        for cue_file in cue_files:
            audio_file = None
            for ext in ALL_AUDIO_EXTS:
                candidate = cue_file.with_suffix(ext)
                if candidate.exists():
                    audio_file = candidate
                    break

            if not audio_file:
                continue

            cue_tmp = tmp_path / cue_file.stem
            cue_tmp.mkdir(exist_ok=True)
            wav_tracks = split_cue(cue_file, audio_file, cue_tmp)

            for wav in wav_tracks:
                if task_idx >= len(plan.tasks):
                    break
                task = plan.tasks[task_idx]
                logger.debug(
                    "Processing CUE track index=%d source=%s destination=%s",
                    task_idx,
                    wav,
                    task.destination,
                )
                ffmpeg_convert(wav, task.destination, task.codec)
                write_tags(task.destination, task.tags, plan.cover_data)
                if on_progress:
                    on_progress(task_idx, len(plan.tasks), task)
                task_idx += 1


def _execute_track_plan(
    plan: ConversionPlan,
    on_progress: Any,
) -> None:
    plan.output_dir.mkdir(parents=True, exist_ok=True)

    for i, task in enumerate(plan.tasks):
        if task.skipped:
            continue
        logger.debug(
            "Processing track index=%d source=%s destination=%s",
            i,
            task.source,
            task.destination,
        )
        ffmpeg_convert(task.source, task.destination, task.codec)
        write_tags(task.destination, task.tags, plan.cover_data)
        if on_progress:
            on_progress(i, len(plan.tasks), task)
