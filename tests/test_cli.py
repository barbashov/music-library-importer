from unittest.mock import patch

from typer.testing import CliRunner

from music_importer.cli import (
    _build_lookup_attempts,
    _guess_artist_from_filenames,
    _normalize_album_guess,
    app,
)

runner = CliRunner()


class TestCli:
    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # Typer shows help with exit code 0, but subcommand-required returns 2
        assert result.exit_code in (0, 2)

    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_import_help_includes_debug(self):
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "debug logging for troubleshooting" in output
        assert "http timeout in seconds for musicbrainz" in output

    def test_import_nonexistent_dir(self):
        result = runner.invoke(app, ["import", "/nonexistent/dir", "/tmp/output"])
        assert result.exit_code != 0

    @patch("music_importer.cli.check_external_tools", return_value=["ffmpeg", "ffprobe"])
    def test_missing_tools_exits(self, mock_check, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        result = runner.invoke(app, ["import", str(input_dir), str(tmp_path / "output")])
        assert result.exit_code == 1
        assert "Missing" in result.output

    def test_invalid_format(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        with patch("music_importer.cli.check_external_tools", return_value=[]):
            result = runner.invoke(
                app,
                ["import", str(input_dir), str(tmp_path / "output"), "--format", "invalid"],
            )
            assert result.exit_code == 1
            assert "Invalid format" in result.output

    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.MusicBrainzClient")
    def test_dry_run_no_files(self, mock_mb, mock_tools, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "readme.txt").touch()

        result = runner.invoke(
            app,
            [
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--no-tags",
            ],
        )
        assert result.exit_code == 1
        assert "No audio files" in result.output

    @patch("music_importer.cli.check_external_tools", return_value=[])
    def test_global_debug_flag_is_accepted(self, mock_tools, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "--debug",
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--no-tags",
            ],
        )
        assert result.exit_code == 1
        assert "No audio files" in result.output

    def test_debug_and_quiet_conflict(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        result = runner.invoke(
            app,
            ["import", str(input_dir), str(tmp_path / "output"), "--debug", "--quiet"],
        )
        assert result.exit_code == 1
        assert "--debug cannot be used with --quiet" in result.output

    def test_http_timeout_must_be_positive(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        result = runner.invoke(
            app,
            ["import", str(input_dir), str(tmp_path / "output"), "--http-timeout", "0"],
        )
        assert result.exit_code == 1
        assert "--http-timeout must be greater than 0" in result.output

    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.MusicBrainzClient")
    def test_dry_run_with_files(self, mock_mb_cls, mock_tools, tmp_path):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        mock_mb = mock_mb_cls.return_value
        mock_mb.search_release.return_value = None

        result = runner.invoke(
            app,
            [
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--no-tags",
                "--format",
                "alac",
            ],
        )
        assert result.exit_code == 0
        assert "Album" in result.output
        assert "Conversion Plan" in result.output

    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.MusicBrainzClient")
    def test_http_timeout_passed_to_musicbrainz_client(self, mock_mb_cls, mock_tools, tmp_path):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()
        mock_mb = mock_mb_cls.return_value
        mock_mb.search_release.return_value = None

        result = runner.invoke(
            app,
            [
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--http-timeout",
                "7",
            ],
        )
        assert result.exit_code == 0
        mock_mb_cls.assert_called_once()
        assert mock_mb_cls.call_args.kwargs.get("http_timeout") == 7.0

    @patch("music_importer.cli.check_external_tools", return_value=[])
    def test_existing_output_dir_fails(self, mock_tools, tmp_path):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()

        output_root = tmp_path / "output"
        # Pre-create the output dir to trigger conflict
        (output_root / "Artist" / "Album").mkdir(parents=True)

        result = runner.invoke(
            app,
            [
                "import",
                str(input_dir),
                str(output_root),
                "--no-tags",
                "--format",
                "alac",
            ],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_lookup_attempt_order_and_dedup(self):
        assert _build_lookup_attempts("A", "B", "C") == [("A", "B"), ("C", "B"), (None, "B")]
        assert _build_lookup_attempts("A", "B", "A") == [("A", "B"), (None, "B")]

    def test_normalize_album_guess_rejects_generic_name(self):
        assert _normalize_album_guess("input") is None
        assert _normalize_album_guess("Real Album") == "Real Album"

    def test_guess_artist_from_filenames_uses_consensus(self, tmp_path):
        files = [
            tmp_path / "01 - The Beatles - Come Together.flac",
            tmp_path / "02 - The Beatles - Something.flac",
            tmp_path / "03 - The Beatles - Oh! Darling.flac",
        ]
        for file_path in files:
            file_path.touch()
        assert _guess_artist_from_filenames(files) == "The Beatles"

    @patch("music_importer.cli.read_source_tags")
    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.MusicBrainzClient")
    def test_skips_musicbrainz_when_album_guess_is_not_meaningful(
        self, mock_mb_cls, _mock_tools, mock_read_tags, tmp_path
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "01.flac").touch()
        mock_read_tags.return_value = {
            "title": "",
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "albumartist": "",
            "date": "",
            "genre": "",
            "track": 0,
            "total_tracks": 0,
            "disc": 0,
            "total_discs": 0,
        }

        result = runner.invoke(
            app, ["import", str(input_dir), str(tmp_path / "output"), "--dry-run"]
        )

        assert result.exit_code == 0
        assert "Skipping MusicBrainz" in result.output
        mock_mb = mock_mb_cls.return_value
        assert mock_mb.search_release.call_count == 0
        assert mock_mb.search_releases.call_count == 0
