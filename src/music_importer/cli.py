from __future__ import annotations

import json
import logging
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.tree import Tree

from music_importer import __version__
from music_importer.config import DEFAULT_COMPILATIONS_DIR
from music_importer.converter import build_plan, execute_plan, parse_cue_track_count
from music_importer.debug import configure_debug_logging
from music_importer.models import ConversionPlan, ReleaseInfo
from music_importer.musicbrainz import MusicBrainzClient, ReleaseSelectionHints
from music_importer.tagger import read_source_tags
from music_importer.utils import (
    check_cue_dependencies,
    check_external_tools,
    detect_disc_subdirs,
    find_audio_files,
    find_cue_files,
    has_audio_subdirs,
    infer_artist_album,
    is_generic_dir_name,
    is_placeholder_value,
    normalize_metadata_value,
    sanitize_filename,
)

app = typer.Typer(
    name="music-importer",
    help="Convert audio albums to ALAC/AAC with MusicBrainz tagging.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()
logger = logging.getLogger(__name__)
_MAX_PROBE_FILES = 30
_FILENAME_TRACK_PREFIX_RE = re.compile(r"^\s*\d{1,2}(?:[-_. ]+\d{1,2})?\s*[-_. ]+\s*")


def version_callback(value: bool) -> None:
    if value:
        console.print(f"music-importer {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug logging for troubleshooting.",
    ),
    version: bool = typer.Option(
        False, "--version", "-V", callback=version_callback, is_eager=True, help="Show version."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON output for commands."
    ),
) -> None:
    """Music Library Importer - convert and tag audio albums."""
    ctx.obj = ctx.obj or {}
    ctx.obj["debug"] = debug
    ctx.obj["json"] = json_output
    configure_debug_logging(debug)


@app.command(name="import")
def import_album(
    ctx: typer.Context,
    input_dir: Path = typer.Argument(
        ..., help="Input album directory.", exists=True, file_okay=False, resolve_path=True
    ),
    output_root: Path = typer.Argument(
        ..., help="Output music library root directory.", resolve_path=True
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show plan without executing."),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing album directory."
    ),
    format: str = typer.Option("auto", "--format", "-f", help="Output format: alac, aac, or auto."),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Interactively select MusicBrainz match."
    ),
    compilations_dir: str = typer.Option(
        DEFAULT_COMPILATIONS_DIR,
        "--compilations-dir",
        help="Directory name for compilations/VA albums.",
    ),
    no_artwork: bool = typer.Option(False, "--no-artwork", help="Skip cover art embedding."),
    no_tags: bool = typer.Option(False, "--no-tags", help="Skip MusicBrainz tagging."),
    http_timeout: float = typer.Option(
        15.0,
        "--http-timeout",
        help="HTTP timeout in seconds for MusicBrainz and cover art requests.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging for troubleshooting."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error output."),
) -> None:
    """Import and convert an album directory to ALAC/AAC with MusicBrainz tags."""
    json_mode = bool((ctx.obj or {}).get("json"))
    effective_debug = debug or bool((ctx.obj or {}).get("debug"))
    mode = "dry-run" if dry_run else "execute"
    output_dir: Path | None = None
    metadata: dict[str, Any] | None = None
    json_warnings: list[str] = []
    release_info: ReleaseInfo | None = None
    plan: ConversionPlan | None = None
    completed_tracks = 0

    def exit_json_error(
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        _emit_json(
            _build_import_result(
                ok=False,
                mode=mode,
                input_dir=input_dir,
                output_root=output_root,
                output_dir=output_dir,
                metadata=metadata,
                release_info=release_info,
                plan=plan,
                warnings=json_warnings,
                tracks_completed=completed_tracks,
                error={"code": code, "message": message, "details": details or {}},
            )
        )
        raise typer.Exit(1)

    if effective_debug and quiet:
        if json_mode:
            exit_json_error("debug_quiet_conflict", "--debug cannot be used with --quiet.")
        console.print("[red]Error:[/red] --debug cannot be used with --quiet.")
        raise typer.Exit(1)

    configure_debug_logging(effective_debug)
    if json_mode and not effective_debug:
        logging.getLogger("music_importer").setLevel(logging.INFO)
    verbose = verbose or effective_debug
    logger.debug(
        "Starting import input_dir=%s output_root=%s dry_run=%s overwrite=%s format=%s "
        "interactive=%s no_artwork=%s no_tags=%s http_timeout=%s verbose=%s quiet=%s debug=%s",
        input_dir,
        output_root,
        dry_run,
        overwrite,
        format,
        interactive,
        no_artwork,
        no_tags,
        http_timeout,
        verbose,
        quiet,
        effective_debug,
    )
    if interactive and json_mode:
        exit_json_error(
            "interactive_not_supported_in_json",
            "--interactive is not supported with --json.",
        )

    # Validate format
    force_format: str | None = None
    if format == "auto":
        force_format = None
    elif format in ("alac", "aac"):
        force_format = format
    else:
        if json_mode:
            exit_json_error(
                "invalid_format",
                f"Invalid format '{format}'. Use: alac, aac, or auto.",
                details={"format": format},
            )
        console.print(f"[red]Error:[/red] Invalid format '{format}'. Use: alac, aac, or auto.")
        raise typer.Exit(1)
    if http_timeout <= 0:
        if json_mode:
            exit_json_error(
                "invalid_http_timeout",
                "--http-timeout must be greater than 0.",
                details={"http_timeout": http_timeout},
            )
        console.print("[red]Error:[/red] --http-timeout must be greater than 0.")
        raise typer.Exit(1)

    # Check external tools
    missing = check_external_tools()
    for tool in check_cue_dependencies(input_dir):
        if tool not in missing:
            missing.append(tool)
    if missing:
        if json_mode:
            exit_json_error(
                "missing_dependencies",
                "Missing required tools.",
                details={"missing_tools": missing},
            )
        console.print(
            Panel(
                f"[red]Missing required tools:[/red] {', '.join(missing)}\n\n"
                "Install with:\n"
                "  [cyan]sudo apt install ffmpeg[/cyan] (Ubuntu)\n"
                "  [cyan]brew install ffmpeg[/cyan] (macOS)",
                title="Missing Dependencies",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    # Infer metadata candidates from directory, source tags, and filenames.
    dir_artist_guess, dir_album_guess = infer_artist_album(input_dir)
    probe_files = _collect_probe_audio_files(input_dir, _MAX_PROBE_FILES)
    tag_artist_guess, tag_album_guess = _guess_from_source_tags(probe_files)
    filename_artist_guess = _guess_artist_from_filenames(probe_files)

    artist_guess = _normalize_artist_guess(tag_artist_guess) or _normalize_artist_guess(
        dir_artist_guess
    )
    album_guess = _normalize_album_guess(tag_album_guess) or _normalize_album_guess(dir_album_guess)

    # MusicBrainz lookup
    cover_data = None

    if not no_tags:
        mb_client = MusicBrainzClient(
            console=None if quiet or json_mode else console,
            http_timeout=http_timeout,
        )

        if not album_guess:
            json_warnings.append("Skipping MusicBrainz: insufficient album metadata for lookup.")
            if not quiet and not json_mode:
                console.print(
                    "[yellow]Skipping MusicBrainz:[/yellow] insufficient album metadata for lookup."
                )
        else:
            attempts = _build_lookup_attempts(artist_guess, album_guess, filename_artist_guess)
            selection_hints = _build_release_selection_hints(input_dir, probe_files)
            logger.debug(
                "MusicBrainz selection hints expected_discs=%s expected_tracks=%s",
                selection_hints.expected_discs if selection_hints else None,
                selection_hints.expected_tracks if selection_hints else None,
            )
            if interactive:
                for query_artist, query_album in attempts:
                    if not quiet and not json_mode:
                        console.print(
                            f"[dim]Searching MusicBrainz:[/dim] "
                            f"{_format_lookup_query(query_artist, query_album)}"
                        )
                    release_info = _interactive_mb_search(
                        mb_client, query_artist, query_album, quiet
                    )
                    if release_info:
                        break
            else:
                for query_artist, query_album in attempts:
                    if not quiet and not json_mode:
                        console.print(
                            f"[dim]Searching MusicBrainz:[/dim] "
                            f"{_format_lookup_query(query_artist, query_album)}"
                        )
                    release = mb_client.search_release(
                        query_artist,
                        query_album,
                        hints=selection_hints,
                    )
                    if not release:
                        continue
                    release_id = release["id"]
                    if not quiet and not json_mode:
                        console.print(
                            f"[green]Found:[/green] {release.get('title')} "
                            f"[dim](id: {release_id})[/dim]"
                        )
                    release_info = mb_client.get_release_details(release_id)
                    break

            if not release_info:
                json_warnings.append("No MusicBrainz match found.")
            if not release_info and not quiet and not json_mode:
                console.print("[yellow]No MusicBrainz match found.[/yellow]")

        if release_info and not no_artwork:
            cover_data = mb_client.get_cover_art(
                release_info.release_id, release_info.release_group_id
            )
            if cover_data and not quiet and not json_mode:
                console.print(f"[green]Cover art:[/green] {len(cover_data) // 1024} KB")
            elif not cover_data:
                json_warnings.append("No cover art found.")
            if not cover_data and not quiet and not json_mode:
                console.print("[yellow]No cover art found.[/yellow]")

    # Resolve final metadata
    if release_info:
        album_title = release_info.title
        album_artist = release_info.artist
        year = release_info.year
        genre = release_info.genre
    else:
        album_title = album_guess or "Unknown Album"
        album_artist = artist_guess or filename_artist_guess or "Unknown Artist"
        year = ""
        genre = ""
        # Try to get year from source tags
        if probe_files:
            src_tags = read_source_tags(probe_files[0])
            if src_tags["date"]:
                year = src_tags["date"][:4]
            if src_tags["genre"]:
                genre = src_tags["genre"]

    # Handle compilations
    if album_artist.lower() == "various artists":
        artist_dir = sanitize_filename(compilations_dir)
    else:
        artist_dir = sanitize_filename(album_artist)

    album_dir = sanitize_filename(album_title)
    output_dir = output_root / artist_dir / album_dir
    metadata = {
        "artist": album_artist,
        "album": album_title,
        "year": year,
        "genre": genre,
        "metadata_source": "musicbrainz" if release_info else "filename",
    }

    # Check existing destination
    if output_dir.exists() and not dry_run and not overwrite:
        if json_mode:
            exit_json_error(
                "output_conflict",
                f"Album directory already exists: {output_dir}",
                details={"output_dir": str(output_dir)},
            )
        console.print(
            Panel(
                f"[red]Album directory already exists:[/red]\n{output_dir}\n\n"
                "Remove it first, choose a different output, or use --overwrite.",
                title="Conflict",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    # Build plan
    plan = build_plan(
        input_dir=input_dir,
        output_dir=output_dir,
        release_info=release_info,
        artist=album_artist,
        album=album_title,
        year=year,
        genre=genre,
        force_format=force_format,
        dry_run=dry_run,
    )
    plan.cover_data = cover_data
    metadata["metadata_source"] = plan.metadata_source

    if not plan.tasks:
        if json_mode:
            exit_json_error(
                "no_audio_files",
                "No audio files found to process.",
                details={"plan_warnings": plan.warnings},
            )
        console.print("[red]Error:[/red] No audio files found to process.")
        if plan.warnings:
            for w in plan.warnings:
                console.print(f"  [yellow]Warning:[/yellow] {w}")
        raise typer.Exit(1)

    if dry_run:
        if json_mode:
            if output_dir.exists():
                msg = (
                    f"Output directory will be overwritten: {output_dir}"
                    if overwrite
                    else f"Output directory already exists: {output_dir}"
                )
                json_warnings.append(msg)
            _emit_json(
                _build_import_result(
                    ok=True,
                    mode=mode,
                    input_dir=input_dir,
                    output_root=output_root,
                    output_dir=output_dir,
                    metadata=metadata,
                    release_info=release_info,
                    plan=plan,
                    warnings=json_warnings,
                    tracks_completed=0,
                )
            )
            return
        display_plan(plan, verbose)
        if output_dir.exists():
            if overwrite:
                console.print(
                    f"\n[yellow]Warning:[/yellow] Output directory will be overwritten: "
                    f"{output_dir}"
                )
            else:
                console.print(
                    f"\n[yellow]Warning:[/yellow] Output directory already exists: {output_dir}"
                )
        return

    # Execute
    if not quiet and not json_mode:
        _print_album_header(plan)

    if output_dir is None:  # pragma: no cover - defensive
        raise RuntimeError("Output directory was not resolved before execution.")

    if json_mode:

        def on_progress(idx: int, total: int, task: object) -> None:
            del total, task
            nonlocal completed_tracks
            completed_tracks = idx + 1

        try:
            execute_plan(plan, on_progress=on_progress, overwrite=overwrite)
        except subprocess.CalledProcessError as exc:
            failed_task = (
                _task_to_json(plan.tasks[completed_tracks], completed_tracks + 1)
                if completed_tracks < len(plan.tasks)
                else None
            )
            exit_json_error(
                "conversion_failed",
                "Conversion command failed.",
                details={
                    "returncode": exc.returncode,
                    "command": [str(part) for part in exc.cmd] if exc.cmd else [],
                    "stderr": _coerce_process_output(exc.stderr),
                    "stdout": _coerce_process_output(exc.output),
                    "failed_track": failed_task,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive safety net
            failed_task = (
                _task_to_json(plan.tasks[completed_tracks], completed_tracks + 1)
                if completed_tracks < len(plan.tasks)
                else None
            )
            exit_json_error(
                "unexpected_error",
                str(exc),
                details={"exception_type": type(exc).__name__, "failed_track": failed_task},
            )

        _emit_json(
            _build_import_result(
                ok=True,
                mode=mode,
                input_dir=input_dir,
                output_root=output_root,
                output_dir=output_dir,
                metadata=metadata,
                release_info=release_info,
                plan=plan,
                warnings=json_warnings,
                tracks_completed=completed_tracks,
            )
        )
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
        disable=quiet,
    ) as progress:
        task_id = progress.add_task("Converting...", total=len(plan.tasks))

        def on_progress(idx: int, total: int, task: object) -> None:
            del idx, total
            title = getattr(task, "tags", {}).get("title", "")
            progress.update(task_id, advance=1, description=f"Converting: {title}")

        execute_plan(plan, on_progress=on_progress, overwrite=overwrite)

    if not quiet:
        console.print(
            Panel(
                f"[green]Done![/green] {len(plan.tasks)} tracks converted.\nOutput: {output_dir}",
                border_style="green",
            )
        )


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _coerce_process_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _task_to_json(task: object, index: int) -> dict[str, Any]:
    source = getattr(task, "source", "")
    destination = getattr(task, "destination", "")
    codec = getattr(task, "codec", "")
    tags = getattr(task, "tags", {})
    return {
        "index": index,
        "source": str(source),
        "destination": str(destination),
        "codec": codec,
        "tags": tags if isinstance(tags, dict) else {},
    }


def _build_import_result(
    *,
    ok: bool,
    mode: str,
    input_dir: Path,
    output_root: Path,
    output_dir: Path | None,
    metadata: dict[str, Any] | None,
    release_info: ReleaseInfo | None,
    plan: ConversionPlan | None,
    warnings: list[str],
    tracks_completed: int,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    track_payload = (
        [_task_to_json(task, idx + 1) for idx, task in enumerate(plan.tasks)] if plan else []
    )
    track_total = len(track_payload)
    return {
        "ok": ok,
        "command": "import",
        "mode": mode,
        "input_dir": str(input_dir),
        "output_root": str(output_root),
        "output_dir": str(output_dir) if output_dir else None,
        "metadata": metadata
        or {
            "artist": "",
            "album": "",
            "year": "",
            "genre": "",
            "metadata_source": "",
        },
        "musicbrainz": {
            "matched": release_info is not None,
            "release_id": release_info.release_id if release_info else None,
            "release_group_id": release_info.release_group_id if release_info else None,
        },
        "cover_art": {
            "present": bool(plan and plan.cover_data),
            "bytes": len(plan.cover_data) if plan and plan.cover_data else 0,
        },
        "summary": {
            "dry_run": mode == "dry-run",
            "tracks_total": track_total,
            "tracks_completed": min(tracks_completed, track_total),
        },
        "tracks": track_payload,
        "warnings": [*warnings, *(plan.warnings if plan else [])],
        "error": error,
    }


def _normalize_artist_guess(value: str | None) -> str | None:
    normalized = normalize_metadata_value(value)
    if not normalized or is_placeholder_value(normalized) or is_generic_dir_name(normalized):
        return None
    return normalized


def _normalize_album_guess(value: str | None) -> str | None:
    normalized = normalize_metadata_value(value)
    if not normalized or is_placeholder_value(normalized) or is_generic_dir_name(normalized):
        return None
    return normalized


def _collect_probe_audio_files(input_dir: Path, limit: int) -> list[Path]:
    files: list[Path] = []
    files.extend(find_audio_files(input_dir))
    if len(files) >= limit:
        return files[:limit]

    for subdir in has_audio_subdirs(input_dir):
        files.extend(find_audio_files(subdir))
        if len(files) >= limit:
            break
    return files[:limit]


def _guess_from_source_tags(probe_files: list[Path]) -> tuple[str | None, str | None]:
    artist_counts: Counter[str] = Counter()
    album_counts: Counter[str] = Counter()

    for file_path in probe_files:
        tags = read_source_tags(file_path)
        album_guess = _normalize_album_guess(tags.get("album"))
        if album_guess:
            album_counts[album_guess] += 1

        albumartist_guess = _normalize_artist_guess(tags.get("albumartist"))
        artist_guess = albumartist_guess or _normalize_artist_guess(tags.get("artist"))
        if artist_guess:
            artist_counts[artist_guess] += 1

    top_artist = artist_counts.most_common(1)[0][0] if artist_counts else None
    top_album = album_counts.most_common(1)[0][0] if album_counts else None
    return top_artist, top_album


def _guess_artist_from_filenames(probe_files: list[Path]) -> str | None:
    artist_counts: Counter[str] = Counter()
    parsed = 0

    for file_path in probe_files:
        stem = _FILENAME_TRACK_PREFIX_RE.sub("", file_path.stem).strip()
        if " - " not in stem:
            continue
        parsed += 1
        candidate = _normalize_artist_guess(stem.split(" - ", 1)[0])
        if candidate:
            artist_counts[candidate] += 1

    if not artist_counts or parsed == 0:
        return None
    top_artist, top_count = artist_counts.most_common(1)[0]
    if top_count >= 2 and (top_count / parsed) >= 0.6:
        return top_artist
    return None


def _build_lookup_attempts(
    artist_guess: str | None, album_guess: str, filename_artist_guess: str | None
) -> list[tuple[str | None, str]]:
    attempts: list[tuple[str | None, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_attempt(artist: str | None, album: str) -> None:
        key = ((artist or "").casefold(), album.casefold())
        if key in seen:
            return
        seen.add(key)
        attempts.append((artist, album))

    add_attempt(artist_guess, album_guess)
    if filename_artist_guess:
        add_attempt(filename_artist_guess, album_guess)
    add_attempt(None, album_guess)
    return attempts


def _build_release_selection_hints(
    input_dir: Path, probe_files: list[Path]
) -> ReleaseSelectionHints | None:
    cue_files = find_cue_files(input_dir)
    if cue_files:
        track_total = sum(
            count for count in (parse_cue_track_count(cue) for cue in cue_files) if count > 0
        )
        return ReleaseSelectionHints(
            expected_discs=len(cue_files),
            expected_tracks=track_total or None,
        )

    expected_discs: int | None = None
    expected_tracks: int | None = None

    direct_files = find_audio_files(input_dir)
    if direct_files:
        expected_tracks = len(direct_files)
        expected_discs = 1
    else:
        disc_subdirs = detect_disc_subdirs(input_dir)
        if disc_subdirs:
            expected_discs = len(disc_subdirs)
            expected_tracks = sum(len(find_audio_files(path)) for _, path in disc_subdirs) or None
        else:
            audio_subdirs = has_audio_subdirs(input_dir)
            if audio_subdirs:
                expected_discs = len(audio_subdirs)
                expected_tracks = sum(len(find_audio_files(path)) for path in audio_subdirs) or None

    total_discs_counter: Counter[int] = Counter()
    max_disc_tag = 0
    for file_path in probe_files:
        tags = read_source_tags(file_path)
        total_discs = int(tags.get("total_discs", 0) or 0)
        disc_num = int(tags.get("disc", 0) or 0)
        if total_discs > 0:
            total_discs_counter[total_discs] += 1
        if disc_num > max_disc_tag:
            max_disc_tag = disc_num
    if total_discs_counter:
        expected_discs = total_discs_counter.most_common(1)[0][0]
    elif max_disc_tag > 1:
        expected_discs = max_disc_tag

    if expected_discs is None and expected_tracks is None:
        return None
    return ReleaseSelectionHints(expected_discs=expected_discs, expected_tracks=expected_tracks)


def _format_lookup_query(artist: str | None, album: str) -> str:
    return f"{artist or 'Any Artist'} — {album}"


def _interactive_mb_search(
    mb_client: MusicBrainzClient, artist: str | None, album: str, quiet: bool
) -> ReleaseInfo | None:
    """Show top MB results and let user pick."""
    releases = mb_client.search_releases(artist, album, limit=5)
    if not releases:
        if not quiet:
            console.print("[yellow]No MusicBrainz results found.[/yellow]")
        return None

    table = Table(title="MusicBrainz Results", show_lines=True)
    table.add_column("#", style="bold", width=3)
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Year", width=6)
    table.add_column("Country", width=4)
    table.add_column("Tracks", width=6)

    for i, r in enumerate(releases, 1):
        date = r.get("date", "")[:4]
        country = r.get("country", "")
        artist_credit = r.get("artist-credit-phrase", "")
        media = r.get("medium-list", [])
        track_count = sum(int(m.get("track-count", 0)) for m in media)
        table.add_row(str(i), r.get("title", ""), artist_credit, date, country, str(track_count))

    console.print(table)
    console.print("[dim]Enter number to select, or 0 to skip:[/dim]")

    try:
        choice = int(input("> "))
    except (ValueError, EOFError):
        choice = 0

    if choice < 1 or choice > len(releases):
        return None

    selected = releases[choice - 1]
    return mb_client.get_release_details(selected["id"])


def display_plan(plan: ConversionPlan, verbose: bool = False) -> None:
    """Display a conversion plan using rich components."""
    _print_album_header(plan)

    # Directory tree
    tree = Tree(f"[bold]{plan.output_dir.parent.parent.name}/[/bold]")
    artist_branch = tree.add(f"[cyan]{plan.output_dir.parent.name}/[/cyan]")
    album_branch = artist_branch.add(f"[cyan]{plan.output_dir.name}/[/cyan]")

    for task in plan.tasks:
        codec_badge = "[green]ALAC[/green]" if task.codec == "alac" else "[blue]AAC[/blue]"
        album_branch.add(f"{task.destination.name}  {codec_badge}")

    console.print(tree)
    console.print()

    # Track table
    table = Table(title="Conversion Plan", show_lines=True)
    table.add_column("#", style="bold", width=5)
    table.add_column("Source", style="dim", max_width=30)
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Codec", width=5)

    for task in plan.tasks:
        disc = task.tags.get("disc", 1)
        track = task.tags.get("track", 0)
        codec_str = "[green]ALAC[/green]" if task.codec == "alac" else "[blue]AAC[/blue]"
        table.add_row(
            f"{disc}-{track:02d}",
            task.source.name,
            task.tags.get("title", ""),
            task.tags.get("artist", ""),
            codec_str,
        )

    console.print(table)
    console.print()

    # Cover art status
    if plan.cover_data:
        console.print(f"[green]Cover art:[/green] {len(plan.cover_data) // 1024} KB")
    else:
        console.print("[yellow]No cover art[/yellow]")

    # Metadata source
    source_colors = {
        "musicbrainz": "green",
        "source_tags": "yellow",
        "filename": "red",
    }
    color = source_colors.get(plan.metadata_source, "white")
    console.print(f"Metadata source: [{color}]{plan.metadata_source}[/{color}]")

    # Warnings
    if plan.warnings:
        console.print()
        for w in plan.warnings:
            console.print(f"  [yellow]Warning:[/yellow] {w}")

    console.print(f"\n[dim]Total: {len(plan.tasks)} tracks[/dim]")


def _print_album_header(plan: ConversionPlan) -> None:
    lines = [
        f"[bold]{plan.album}[/bold]",
        f"by [cyan]{plan.artist}[/cyan]",
    ]
    if plan.year:
        lines.append(f"Year: {plan.year}")
    if plan.genre:
        lines.append(f"Genre: {plan.genre}")
    lines.append(f"Source: {plan.input_dir}")
    lines.append(f"Output: {plan.output_dir}")

    console.print(Panel("\n".join(lines), title="Album", border_style="blue"))
