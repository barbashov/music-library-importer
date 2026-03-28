from unittest.mock import patch

from typer.testing import CliRunner

from music_importer.cli import app

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
