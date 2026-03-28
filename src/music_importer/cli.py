from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.tree import Tree

from music_importer import __version__
from music_importer.config import DEFAULT_COMPILATIONS_DIR
from music_importer.converter import build_plan, execute_plan
from music_importer.debug import configure_debug_logging
from music_importer.models import ConversionPlan, ReleaseInfo
from music_importer.musicbrainz import MusicBrainzClient
from music_importer.tagger import read_source_tags
from music_importer.utils import (
    check_external_tools,
    find_audio_files,
    has_audio_subdirs,
    infer_artist_album,
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
) -> None:
    """Music Library Importer - convert and tag audio albums."""
    ctx.obj = ctx.obj or {}
    ctx.obj["debug"] = debug
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
    effective_debug = debug or bool((ctx.obj or {}).get("debug"))
    if effective_debug and quiet:
        console.print("[red]Error:[/red] --debug cannot be used with --quiet.")
        raise typer.Exit(1)

    configure_debug_logging(effective_debug)
    verbose = verbose or effective_debug
    logger.debug(
        "Starting import input_dir=%s output_root=%s dry_run=%s format=%s interactive=%s "
        "no_artwork=%s no_tags=%s http_timeout=%s verbose=%s quiet=%s debug=%s",
        input_dir,
        output_root,
        dry_run,
        format,
        interactive,
        no_artwork,
        no_tags,
        http_timeout,
        verbose,
        quiet,
        effective_debug,
    )

    # Validate format
    force_format: str | None = None
    if format == "auto":
        force_format = None
    elif format in ("alac", "aac"):
        force_format = format
    else:
        console.print(f"[red]Error:[/red] Invalid format '{format}'. Use: alac, aac, or auto.")
        raise typer.Exit(1)
    if http_timeout <= 0:
        console.print("[red]Error:[/red] --http-timeout must be greater than 0.")
        raise typer.Exit(1)

    # Check external tools
    missing = check_external_tools()
    if missing:
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

    # Infer artist and album
    artist_guess, album_guess = infer_artist_album(input_dir)

    # Try source tags for better initial guess
    audio_files = find_audio_files(input_dir)
    if not audio_files:
        subdirs = has_audio_subdirs(input_dir)
        if subdirs:
            audio_files = find_audio_files(subdirs[0])

    if audio_files:
        src_tags = read_source_tags(audio_files[0])
        if src_tags["album"]:
            album_guess = src_tags["album"]
        if src_tags["albumartist"]:
            artist_guess = src_tags["albumartist"]
        elif src_tags["artist"]:
            artist_guess = src_tags["artist"]

    # MusicBrainz lookup
    release_info = None
    cover_data = None

    if not no_tags:
        mb_client = MusicBrainzClient(
            console=None if quiet else console,
            http_timeout=http_timeout,
        )

        if not quiet:
            console.print(f"[dim]Searching MusicBrainz:[/dim] {artist_guess} — {album_guess}")

        if interactive:
            release_info = _interactive_mb_search(mb_client, artist_guess, album_guess, quiet)
        else:
            release = mb_client.search_release(artist_guess, album_guess)
            if release:
                release_id = release["id"]
                if not quiet:
                    console.print(
                        f"[green]Found:[/green] {release.get('title')} "
                        f"[dim](id: {release_id})[/dim]"
                    )
                release_info = mb_client.get_release_details(release_id)
            else:
                if not quiet:
                    console.print("[yellow]No MusicBrainz match found.[/yellow]")

        if release_info and not no_artwork:
            cover_data = mb_client.get_cover_art(
                release_info.release_id, release_info.release_group_id
            )
            if cover_data and not quiet:
                console.print(f"[green]Cover art:[/green] {len(cover_data) // 1024} KB")
            elif not cover_data and not quiet:
                console.print("[yellow]No cover art found.[/yellow]")

    # Resolve final metadata
    if release_info:
        album_title = release_info.title
        album_artist = release_info.artist
        year = release_info.year
        genre = release_info.genre
    else:
        album_title = album_guess
        album_artist = artist_guess
        year = ""
        genre = ""
        # Try to get year from source tags
        if audio_files:
            src_tags = read_source_tags(audio_files[0])
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

    # Check existing destination
    if output_dir.exists() and not dry_run:
        console.print(
            Panel(
                f"[red]Album directory already exists:[/red]\n{output_dir}\n\n"
                "Remove it first or choose a different output.",
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

    if not plan.tasks:
        console.print("[red]Error:[/red] No audio files found to process.")
        if plan.warnings:
            for w in plan.warnings:
                console.print(f"  [yellow]Warning:[/yellow] {w}")
        raise typer.Exit(1)

    if dry_run:
        display_plan(plan, verbose)
        if output_dir.exists():
            console.print(
                f"\n[yellow]Warning:[/yellow] Output directory already exists: {output_dir}"
            )
        return

    # Execute
    if not quiet:
        _print_album_header(plan)

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
            title = getattr(task, "tags", {}).get("title", "")
            progress.update(task_id, advance=1, description=f"Converting: {title}")

        execute_plan(plan, on_progress=on_progress)

    if not quiet:
        console.print(
            Panel(
                f"[green]Done![/green] {len(plan.tasks)} tracks converted.\nOutput: {output_dir}",
                border_style="green",
            )
        )


def _interactive_mb_search(
    mb_client: MusicBrainzClient, artist: str, album: str, quiet: bool
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
