from pathlib import Path
from unittest.mock import patch

import pytest

from music_importer.converter import (
    _build_tags_dict,
    _collect_disc_files,
    build_plan,
    detect_codec,
    parse_cue_file_reference,
    parse_cue_titles,
    parse_cue_track_count,
)
from music_importer.models import ReleaseInfo, TrackInfo


class TestDetectCodec:
    def test_force_alac(self):
        assert detect_codec(Path("test.mp3"), "alac") == "alac"

    def test_force_aac(self):
        assert detect_codec(Path("test.flac"), "aac") == "aac"

    def test_lossless_extension(self):
        assert detect_codec(Path("track.flac")) == "alac"
        assert detect_codec(Path("track.wav")) == "alac"
        assert detect_codec(Path("track.ape")) == "alac"

    @patch("music_importer.converter.subprocess.run")
    def test_ffprobe_lossy_detection(self, mock_run):
        mock_run.return_value.stdout = '{"streams": [{"codec_name": "mp3"}]}'
        assert detect_codec(Path("track.mp3")) == "aac"

    @patch("music_importer.converter.subprocess.run")
    def test_ffprobe_lossless_detection(self, mock_run):
        mock_run.return_value.stdout = '{"streams": [{"codec_name": "flac"}]}'
        assert detect_codec(Path("track.unknown")) == "alac"

    @patch("music_importer.converter.subprocess.run")
    def test_ffprobe_failure_fallback(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        # Non-lossless extension with ffprobe failure → aac
        assert detect_codec(Path("track.unknown")) == "aac"


class TestParseCue:
    def test_parse_titles(self, tmp_path):
        cue = tmp_path / "album.cue"
        cue.write_text(
            'FILE "album.flac" WAVE\n'
            "  TRACK 01 AUDIO\n"
            '    TITLE "First Track"\n'
            "    INDEX 01 00:00:00\n"
            "  TRACK 02 AUDIO\n"
            '    TITLE "Second Track"\n'
            "    INDEX 01 05:30:00\n"
        )
        titles = parse_cue_titles(cue)
        assert titles == ["First Track", "Second Track"]

    def test_parse_track_count(self, tmp_path):
        cue = tmp_path / "album.cue"
        cue.write_text(
            'FILE "album.flac" WAVE\n'
            "  TRACK 01 AUDIO\n"
            '    TITLE "First"\n'
            "    INDEX 01 00:00:00\n"
            "  TRACK 02 AUDIO\n"
            '    TITLE "Second"\n'
            "    INDEX 01 03:00:00\n"
            "  TRACK 03 AUDIO\n"
            '    TITLE "Third"\n'
            "    INDEX 01 06:00:00\n"
        )
        assert parse_cue_track_count(cue) == 3

    def test_empty_cue(self, tmp_path):
        cue = tmp_path / "empty.cue"
        cue.write_text("")
        assert parse_cue_titles(cue) == []
        assert parse_cue_track_count(cue) == 0

    def test_parse_file_reference(self, tmp_path):
        cue = tmp_path / "album.cue"
        cue.write_text('FILE "The Album.flac" WAVE\n  TRACK 01 AUDIO\n')
        assert parse_cue_file_reference(cue) == "The Album.flac"

    def test_parse_file_reference_missing(self, tmp_path):
        cue = tmp_path / "album.cue"
        cue.write_text("  TRACK 01 AUDIO\n")
        assert parse_cue_file_reference(cue) is None


class TestCollectDiscFiles:
    def test_direct_files(self, tmp_path):
        (tmp_path / "01.flac").touch()
        (tmp_path / "02.flac").touch()
        result = _collect_disc_files(tmp_path)
        assert len(result) == 1
        assert result[0][0] == 1
        assert len(result[0][1]) == 2

    def test_disc_subdirs(self, tmp_path):
        cd1 = tmp_path / "CD1"
        cd2 = tmp_path / "CD2"
        cd1.mkdir()
        cd2.mkdir()
        (cd1 / "01.flac").touch()
        (cd2 / "01.flac").touch()
        result = _collect_disc_files(tmp_path)
        assert len(result) == 2
        assert result[0][0] == 1
        assert result[1][0] == 2

    def test_empty_dir(self, tmp_path):
        result = _collect_disc_files(tmp_path)
        assert result == []


class TestBuildPlan:
    def test_basic_plan_with_release_info(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        release = ReleaseInfo(
            release_id="r1",
            release_group_id="rg1",
            title="Album",
            artist="Artist",
            date="2020-01-01",
            year="2020",
            genre="Rock",
            tracks={
                1: TrackInfo("Song One", "Artist", 1, 2, 1, 1),
                2: TrackInfo("Song Two", "Artist", 2, 2, 1, 1),
            },
        )

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=release,
            artist="Artist",
            album="Album",
            year="2020",
            genre="Rock",
            force_format="alac",
        )

        assert len(plan.tasks) == 2
        assert plan.tasks[0].tags["title"] == "Song One"
        assert plan.tasks[1].tags["title"] == "Song Two"
        assert plan.tasks[0].codec == "alac"
        assert plan.metadata_source == "musicbrainz"

    def test_plan_sanitizes_filenames(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "01.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        release = ReleaseInfo(
            release_id="r1",
            release_group_id="rg1",
            title="Album",
            artist="Artist",
            date="2020",
            year="2020",
            genre="",
            tracks={
                1: TrackInfo("What/Is:This?", "Artist", 1, 1, 1, 1),
            },
        )

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=release,
            artist="Artist",
            album="Album",
            year="2020",
            genre="",
            force_format="alac",
        )

        assert len(plan.tasks) == 1
        dest_name = plan.tasks[0].destination.name
        assert "/" not in dest_name
        assert ":" not in dest_name
        assert "?" not in dest_name

    def test_plan_consistent_year(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "01.flac").touch()
        (input_dir / "02.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=None,
            artist="Artist",
            album="Album",
            year="1969",
            genre="",
            force_format="alac",
        )

        # All tracks must have the same year
        years = {task.tags["date"] for task in plan.tasks}
        assert years == {"1969"}

    def test_dry_run_cue_plan(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        cue = input_dir / "album.cue"
        cue.write_text(
            'FILE "album.flac" WAVE\n'
            "  TRACK 01 AUDIO\n"
            '    TITLE "Song A"\n'
            "    INDEX 01 00:00:00\n"
            "  TRACK 02 AUDIO\n"
            '    TITLE "Song B"\n'
            "    INDEX 01 03:00:00\n"
        )
        (input_dir / "album.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=None,
            artist="Artist",
            album="Album",
            year="2020",
            genre="",
            force_format=None,
            dry_run=True,
        )

        assert len(plan.tasks) == 2
        assert plan.tasks[0].tags["title"] == "Song A"
        assert plan.tasks[1].tags["title"] == "Song B"

    def test_no_audio_files_warning(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "readme.txt").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=None,
            artist="Artist",
            album="Album",
            year="",
            genre="",
            force_format=None,
        )

        assert len(plan.tasks) == 0
        assert any("No audio files" in w for w in plan.warnings)

    def test_cue_with_mismatched_single_audio_raises(self, tmp_path):
        """1 CUE + 1 audio file with wrong name → error, not silent single-track import."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        cue = input_dir / "The Album.cue"
        cue.write_text(
            'FILE "The Album.flac" WAVE\n'
            "  TRACK 01 AUDIO\n"
            '    TITLE "Song A"\n'
            "    INDEX 01 00:00:00\n"
            "  TRACK 02 AUDIO\n"
            '    TITLE "Song B"\n'
            "    INDEX 01 03:00:00\n"
        )
        # The actual file has a different name — not what the CUE expects
        (input_dir / "wrongname.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        with pytest.raises(ValueError, match=r"The Album\.flac"):
            build_plan(
                input_dir=input_dir,
                output_dir=output_dir,
                release_info=None,
                artist="Artist",
                album="Album",
                year="2020",
                genre="",
                force_format=None,
            )

    def test_multidisc_cue_with_mismatched_audio_raises(self, tmp_path):
        """2 CUEs + 2 wrong-named FLACs (multi-disc) → error, not silent 2-track import."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        for disc in ("CD1", "CD2"):
            cue = input_dir / f"{disc}.cue"
            cue.write_text(f'FILE "{disc}.flac" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        (input_dir / "disc1_wrongname.flac").touch()
        (input_dir / "disc2_wrongname.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        with pytest.raises(ValueError, match="audio file could not be matched"):
            build_plan(
                input_dir=input_dir,
                output_dir=output_dir,
                release_info=None,
                artist="Artist",
                album="Album",
                year="2020",
                genre="",
                force_format=None,
            )

    def test_cue_without_matching_audio_falls_back_to_individual_tracks(self, tmp_path):
        """CUE present but no matching audio file; multiple FLACs exist → fall back."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        cue = input_dir / "album.cue"
        cue.write_text(
            'FILE "album.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "Song A"\n    INDEX 01 00:00:00\n'
        )
        # Individual pre-split tracks (no single album.flac)
        (input_dir / "01 - Song A.flac").touch()
        (input_dir / "02 - Song B.flac").touch()

        output_dir = tmp_path / "output" / "Artist" / "Album"

        plan = build_plan(
            input_dir=input_dir,
            output_dir=output_dir,
            release_info=None,
            artist="Artist",
            album="Album",
            year="2020",
            genre="",
            force_format="alac",
        )

        # Should have processed the 2 individual files, not the (missing) CUE target
        assert len(plan.tasks) == 2


class TestBuildTagsDict:
    def test_all_fields(self):
        tags = _build_tags_dict(
            title="Song",
            artist="Artist",
            album="Album",
            albumartist="Album Artist",
            date="2020",
            genre="Rock",
            track=1,
            total_tracks=10,
            disc=1,
            total_discs=2,
        )
        assert tags["title"] == "Song"
        assert tags["genre"] == "Rock"
        assert tags["disc"] == 1
        assert tags["total_discs"] == 2
