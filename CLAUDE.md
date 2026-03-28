# CLAUDE.md

## Project overview

Music Library Importer converts audio albums to ALAC/AAC and tags them via MusicBrainz. Outputs to a clean `Artist/Album/` directory structure suitable for Navidrome and other music servers.

## Development setup

```bash
uv sync
uv run music-importer --help
```

## Running tests

```bash
uv run pytest               # all tests
uv run pytest -x -v         # stop on first failure, verbose
uv run pytest tests/test_utils.py  # specific module
```

## Linting, formatting, type checking

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy
```

## Docker

```bash
docker build -t music-importer .
```

Multi-arch (amd64 + arm64) builds are handled by CI and pushed to ghcr.io.

## Architecture

- `src/music_importer/cli.py` — Typer CLI app, orchestration, rich output
- `src/music_importer/musicbrainz.py` — MusicBrainz API client with rate limiting
- `src/music_importer/converter.py` — ffmpeg conversion, CUE splitting, plan building
- `src/music_importer/tagger.py` — mutagen tag writing and source tag reading
- `src/music_importer/models.py` — dataclasses (ReleaseInfo, TrackInfo, ConversionPlan, ConversionTask)
- `src/music_importer/config.py` — constants, audio extensions, environment config
- `src/music_importer/utils.py` — filename sanitization, directory helpers, tool checks

## Key design pattern

**Plan-then-execute**: `build_plan()` creates a `ConversionPlan` dataclass, which is consumed by both `display_plan()` (dry run) and `execute_plan()` (real conversion). This ensures dry run output matches actual behavior.

## Conventions

- Type hints on all function signatures
- Dataclasses over dicts for structured data
- All MusicBrainz interaction goes through `MusicBrainzClient`
- All file I/O goes through `converter.py` or `tagger.py`
- Tests mock external APIs and subprocess calls — no real network or audio conversion in tests
- Use `tmp_path` pytest fixture for filesystem tests
