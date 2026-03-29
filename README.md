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
- **JSON mode**: Machine-readable output for automation (`--json`)
- **Interactive mode**: Choose from multiple MusicBrainz matches
- **Source tag fallback**: Reads existing tags from source files when MusicBrainz lookup fails
- **Pretty CLI**: Rich progress bars, tables, and color-coded output

## Installation (Ubuntu Linux)

### System dependencies

```bash
sudo apt update
sudo apt install -y ffmpeg shntool flac
```

`ffmpeg` and `ffprobe` are required. For CUE sheet splitting, install `shntool` (provides `shnsplit`).
For CUE + FLAC inputs, install `flac` as well (used by `shnsplit` as decoder).

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
2. Look up the album on MusicBrainz (see "How MusicBrainz matching works" below)
3. Convert all tracks to ALAC (lossless) or AAC (lossy input)
4. Tag tracks with MusicBrainz metadata and cover art
5. Save to `library/Artist/Album/` directory

### Dry run (preview changes)

```bash
uv run music-importer import --dry-run /path/to/album /path/to/library
```

### JSON output for automation

```bash
# Dry run JSON
uv run music-importer --json import --dry-run /path/to/album /path/to/library

# Execute JSON
uv run music-importer --json import /path/to/album /path/to/library
```

`--json` prints a single JSON object for `import` (success or error), designed for scripts/agents.
Help output remains regular CLI help text. `--json --version` keeps the current plain-text version output.

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

### How MusicBrainz matching works

Matching is best-effort and uses multiple hints from your input files.

1. Start with directory hints (`Artist/Album` or `Artist - Album`).
2. Read source tags across multiple files and choose consensus `albumartist`/`artist` and `album`.
3. Treat placeholder values (`Unknown Artist`, `Unknown Album`, `n/a`, `none`, etc.) as missing.
4. Ignore generic mount/root directory names (`input`, `output`, `tmp`, `music`, `downloads`, etc.) for matching.
5. If tags are poor, try filename artist hints from patterns like `Artist - Track`.
6. Query MusicBrainz using the best `artist + album`.
7. If needed, retry with filename-derived artist.
8. Retry with album-only lookup when artist is still unknown.
9. In non-interactive mode, release selection uses deterministic ranking:
   - only candidates with the highest MusicBrainz text score are considered;
   - source shape hints are preferred first (disc count and total track count);
   - format is used as a tie-breaker (`CD` > `Digital Media` > other non-vinyl > unknown/mixed > vinyl-only);
   - reissue-like disambiguation is penalized, then earlier release date is preferred.
10. If no match is found, import continues with source-tag/filename fallback metadata.

Timeout behavior:
- Timeouts are shown as concise warnings (no traceback output).
- `--http-timeout` applies to both MusicBrainz and cover art requests.

Proxy behavior:
- Standard `http://` and `https://` proxy URLs continue to work via Python urllib defaults.
- SOCKS proxies are supported for MusicBrainz and cover art via `HTTPS_PROXY` or `ALL_PROXY`.
- Supported SOCKS schemes: `socks5://`, `socks5h://`, `socks4://`, `socks4a://`.
- Use `socks5h://` or `socks4a://` when you want DNS resolution performed by the proxy.

### All options

```
music-importer import <input_dir> <output_root> [options]

Options:
  --json                         Emit machine-readable JSON output for commands
  --dry-run, -n              Show plan without executing
  --format, -f TEXT          Output format: alac, aac, or auto (default: auto)
  --interactive, -i          Interactively select MusicBrainz match
  --compilations-dir TEXT    Directory name for VA albums (default: Compilations)
  --no-artwork               Skip cover art embedding
  --no-tags                  Skip MusicBrainz tagging
  --http-timeout FLOAT       HTTP timeout in seconds for MusicBrainz and cover art (default: 15)
  --debug                    Enable debug logging for troubleshooting
  --verbose, -v              Show detailed output
  --quiet, -q                Suppress non-error output
  --version, -V              Show version
```

`--debug` cannot be combined with `--quiet`.
`--http-timeout` applies per request attempt (MusicBrainz retries remain enabled).

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
  -e HTTPS_PROXY=socks5://10.0.35.20:1080 \
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
- The image includes `ffmpeg`, `shntool`, and `flac` -- no system dependencies needed
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
