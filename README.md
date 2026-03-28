# music-library-importer

Convert audio albums to ALAC/AAC and tag them automatically via MusicBrainz. Designed for use with Navidrome and other music servers that expect a clean `Artist/Album/` directory structure.

## Features

- **Auto-detect lossless/lossy**: FLAC, APE, WAV, AIFF, WV, TTA -> ALAC; MP3, OGG, OPUS, WMA -> AAC (no upsampling)
- **MusicBrainz tagging**: Automatic album/track metadata lookup with cover art
- **CUE sheet support**: Split single-file + CUE into individual tracks
- **Multi-disc albums**: Detects CD1/CD2 subdirectories and multi-disc releases
- **Compilations**: Various Artists albums go into a configurable `Compilations/` directory
- **Navidrome-friendly**: Consistent year tags across all tracks (prevents album splitting)
- **Dry run mode**: Preview all changes before executing
- **Interactive mode**: Choose from multiple MusicBrainz matches
- **Source tag fallback**: Reads existing tags from source files when MusicBrainz lookup fails
- **Pretty CLI**: Rich progress bars, tables, and color-coded output

## Installation (Ubuntu Linux)

### System dependencies

```bash
sudo apt update
sudo apt install -y ffmpeg shntool
```

`ffmpeg` and `ffprobe` are required. `shntool` (provides `shnsplit`) is only needed for CUE sheet splitting.

### Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Clone and install

```bash
git clone https://github.com/barbashov/music-library-importer.git
cd music-library-importer
uv sync
```

### Configuration

Set your email for MusicBrainz API compliance (recommended):

```bash
export MUSIC_IMPORTER_EMAIL=you@example.com
```

Add this to your `~/.bashrc` or `~/.zshrc` to persist it.

## Usage

### Basic import

```bash
uv run music-importer import /path/to/album /path/to/music/library
```

This will:
1. Detect the artist and album from directory structure / file tags
2. Look up the album on MusicBrainz
3. Convert all tracks to ALAC (lossless) or AAC (lossy input)
4. Tag tracks with MusicBrainz metadata and cover art
5. Save to `library/Artist/Album/` directory

### Dry run (preview changes)

```bash
uv run music-importer import --dry-run /path/to/album /path/to/library
```

### Force output format

```bash
# Force AAC output even for lossless sources
uv run music-importer import --format aac /path/to/album /path/to/library

# Force ALAC output
uv run music-importer import --format alac /path/to/album /path/to/library
```

### Interactive MusicBrainz selection

```bash
uv run music-importer import --interactive /path/to/album /path/to/library
```

Shows top 5 MusicBrainz matches in a table and lets you pick the correct one.

### All options

```
music-importer import <input_dir> <output_root> [options]

Options:
  --dry-run, -n              Show plan without executing
  --format, -f TEXT          Output format: alac, aac, or auto (default: auto)
  --interactive, -i          Interactively select MusicBrainz match
  --compilations-dir TEXT    Directory name for VA albums (default: Compilations)
  --no-artwork               Skip cover art embedding
  --no-tags                  Skip MusicBrainz tagging
  --verbose, -v              Show detailed output
  --quiet, -q                Suppress non-error output
  --version, -V              Show version
```

## Supported formats

| Input format | Extension | Output codec |
|-------------|-----------|-------------|
| FLAC | .flac | ALAC |
| APE | .ape | ALAC |
| WAV | .wav | ALAC |
| AIFF | .aiff | ALAC |
| WavPack | .wv | ALAC |
| TTA | .tta | ALAC |
| MP3 | .mp3 | AAC 256k |
| Ogg Vorbis | .ogg | AAC 256k |
| Opus | .opus | AAC 256k |
| WMA | .wma | AAC 256k |

## Directory structure

The tool creates the following structure:

```
output_root/
├── Artist Name/
│   ├── Album Name/
│   │   ├── 1-01 Track Title.m4a
│   │   ├── 1-02 Track Title.m4a
│   │   └── ...
│   └── Another Album/
│       └── ...
└── Compilations/
    └── Various Artists Album/
        └── ...
```

## Docker

### Run from GHCR (no local install needed)

```bash
docker run --rm \
  -v /path/to/album:/input:ro \
  -v /path/to/music/library:/output \
  -e MUSIC_IMPORTER_EMAIL=you@example.com \
  ghcr.io/barbashov/music-library-importer:latest \
  import /input /output
```

### Dry run with Docker

```bash
docker run --rm \
  -v /path/to/album:/input:ro \
  -v /path/to/music/library:/output \
  ghcr.io/barbashov/music-library-importer:latest \
  import --dry-run /input /output
```

### Build locally

```bash
docker build -t music-importer .
docker run --rm \
  -v /path/to/album:/input:ro \
  -v /path/to/music/library:/output \
  music-importer import /input /output
```

### Notes

- Mount your input album as `/input` (read-only `:ro` is recommended)
- Mount your music library root as `/output`
- The image includes `ffmpeg` and `shntool` -- no system dependencies needed
- Multi-arch image: supports both `linux/amd64` and `linux/arm64`
- `latest` tag always points to the most recent build from `main`

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Type check
uv run mypy
```

## License

MIT
