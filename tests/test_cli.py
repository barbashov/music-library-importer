import json
import subprocess
from unittest.mock import patch

from typer.testing import CliRunner

from music_importer.cli import (
    _build_lookup_attempts,
    _build_release_selection_hints,
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

    def test_json_version_remains_plain_text(self):
        result = runner.invoke(app, ["--json", "--version"])
        assert result.exit_code == 0
        assert "music-importer 0.1.0" in result.output
        assert "{" not in result.output

    def test_import_help_includes_debug(self):
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "debug logging for troubleshooting" in output
        assert "http timeout in seconds for musicbrainz" in output

    def test_json_help_remains_help_text(self):
        result = runner.invoke(app, ["--json", "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert '"ok"' not in result.output

    def test_json_import_help_remains_help_text(self):
        result = runner.invoke(app, ["--json", "import", "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert '"ok"' not in result.output

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

    def test_json_invalid_format(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        with patch("music_importer.cli.check_external_tools", return_value=[]):
            result = runner.invoke(
                app,
                [
                    "--json",
                    "import",
                    str(input_dir),
                    str(tmp_path / "output"),
                    "--format",
                    "invalid",
                ],
            )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "invalid_format"

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
    def test_json_no_audio_files_error(self, mock_tools, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "readme.txt").touch()

        result = runner.invoke(
            app,
            [
                "--json",
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--no-tags",
            ],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "no_audio_files"
        assert payload["summary"]["tracks_total"] == 0

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

    def test_json_interactive_rejected(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "--json",
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--interactive",
            ],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "interactive_not_supported_in_json"

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

    @patch("music_importer.cli.read_source_tags")
    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.MusicBrainzClient")
    def test_noninteractive_musicbrainz_search_uses_selection_hints(
        self, mock_mb_cls, _mock_tools, mock_read_tags, tmp_path
    ):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "disc1.flac").touch()
        (input_dir / "disc1.cue").write_text("  TRACK 01 AUDIO\n", encoding="utf-8")
        mock_read_tags.return_value = {
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
        mock_mb = mock_mb_cls.return_value
        mock_mb.search_release.return_value = None

        result = runner.invoke(
            app,
            [
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--dry-run",
                "--format",
                "alac",
            ],
        )

        assert result.exit_code == 0
        _, kwargs = mock_mb.search_release.call_args
        hints = kwargs["hints"]
        assert hints.expected_discs == 1
        assert hints.expected_tracks == 1

    @patch("music_importer.cli.check_external_tools", return_value=[])
    def test_json_dry_run_with_files(self, mock_tools, tmp_path):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        result = runner.invoke(
            app,
            [
                "--json",
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
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["mode"] == "dry-run"
        assert payload["summary"]["tracks_total"] == 2
        assert len(payload["tracks"]) == 2

    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.execute_plan")
    def test_json_execute_success(self, mock_execute_plan, mock_tools, tmp_path):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        def _fake_execute(plan, on_progress=None):
            if on_progress:
                on_progress(0, len(plan.tasks), plan.tasks[0])
                on_progress(1, len(plan.tasks), plan.tasks[1])

        mock_execute_plan.side_effect = _fake_execute

        result = runner.invoke(
            app,
            [
                "--json",
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--no-tags",
                "--format",
                "alac",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["mode"] == "execute"
        assert payload["summary"]["tracks_total"] == 2
        assert payload["summary"]["tracks_completed"] == 2

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

    @patch("music_importer.cli.check_external_tools", return_value=[])
    @patch("music_importer.cli.execute_plan")
    def test_json_execute_failure_reports_partial_progress(
        self, mock_execute_plan, mock_tools, tmp_path
    ):
        input_dir = tmp_path / "Artist" / "Album"
        input_dir.mkdir(parents=True)
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        def _fake_execute(plan, on_progress=None):
            if on_progress:
                on_progress(0, len(plan.tasks), plan.tasks[0])
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["ffmpeg", "-i", "broken.flac"],
                output="stdout text",
                stderr="stderr text",
            )

        mock_execute_plan.side_effect = _fake_execute

        result = runner.invoke(
            app,
            [
                "--json",
                "import",
                str(input_dir),
                str(tmp_path / "output"),
                "--no-tags",
                "--format",
                "alac",
            ],
        )
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "conversion_failed"
        assert payload["summary"]["tracks_total"] == 2
        assert payload["summary"]["tracks_completed"] == 1
        assert payload["error"]["details"]["failed_track"]["index"] == 2

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

    def test_build_release_selection_hints_from_cues(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()
        cue1 = album_dir / "disc1.cue"
        cue2 = album_dir / "disc2.cue"
        cue1.write_text("  TRACK 01 AUDIO\n  TRACK 02 AUDIO\n", encoding="utf-8")
        cue2.write_text("  TRACK 01 AUDIO\n", encoding="utf-8")

        hints = _build_release_selection_hints(album_dir, probe_files=[])

        assert hints is not None
        assert hints.expected_discs == 2
        assert hints.expected_tracks == 3

    @patch("music_importer.cli.read_source_tags")
    def test_build_release_selection_hints_prefers_tag_disc_consensus(
        self, mock_read_tags, tmp_path
    ):
        album_dir = tmp_path / "album"
        album_dir.mkdir()
        file1 = album_dir / "01.flac"
        file2 = album_dir / "02.flac"
        file1.touch()
        file2.touch()
        mock_read_tags.return_value = {
            "title": "",
            "artist": "",
            "album": "",
            "albumartist": "",
            "date": "",
            "genre": "",
            "track": 0,
            "total_tracks": 0,
            "disc": 1,
            "total_discs": 2,
        }

        hints = _build_release_selection_hints(album_dir, probe_files=[file1, file2])

        assert hints is not None
        assert hints.expected_discs == 2
        assert hints.expected_tracks == 2

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
