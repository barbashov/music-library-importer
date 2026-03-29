import contextlib
from pathlib import Path
from unittest.mock import patch

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


class TestSanitizeFilename:
    def test_removes_slashes(self):
        assert sanitize_filename("Track/Title") == "Track_Title"

    def test_removes_colons(self):
        assert sanitize_filename("Re: Something") == "Re_ Something"

    def test_removes_question_marks(self):
        assert sanitize_filename("Why?") == "Why_"

    def test_removes_asterisks(self):
        assert sanitize_filename("Track*3") == "Track_3"

    def test_strips_trailing_dots(self):
        assert sanitize_filename("track...") == "track"

    def test_strips_leading_dots(self):
        assert sanitize_filename("...track") == "track"

    def test_preserves_unicode(self):
        assert sanitize_filename("Café Résumé") == "Café Résumé"

    def test_collapses_multiple_underscores(self):
        assert sanitize_filename("a///b") == "a_b"

    def test_empty_becomes_untitled(self):
        assert sanitize_filename("...") == "Untitled"

    def test_control_chars_removed(self):
        assert sanitize_filename("track\x00name") == "trackname"

    def test_preserves_normal_names(self):
        assert sanitize_filename("01 - Hello World") == "01 - Hello World"

    def test_pipe_replaced(self):
        assert sanitize_filename("A|B") == "A_B"


class TestInferArtistAlbum:
    def test_parent_child(self):
        path = Path("/Music/Beatles/Abbey Road")
        assert infer_artist_album(path) == ("Beatles", "Abbey Road")

    def test_dash_split_from_music_root(self):
        path = Path("/Music/Beatles - Abbey Road")
        assert infer_artist_album(path) == ("Beatles", "Abbey Road")

    def test_dash_split_from_downloads(self):
        path = Path("/downloads/Pink Floyd - The Wall")
        assert infer_artist_album(path) == ("Pink Floyd", "The Wall")

    def test_fallback_unknown_artist(self):
        path = Path("/Some Album")
        assert infer_artist_album(path) == ("Unknown Artist", "Some Album")

    def test_nested_artist_album(self):
        path = Path("/home/user/library/Radiohead/OK Computer")
        assert infer_artist_album(path) == ("Radiohead", "OK Computer")


class TestMetadataValueHelpers:
    def test_normalize_metadata_value(self):
        assert normalize_metadata_value("  A   B  ") == "A B"

    def test_placeholder_value_detection(self):
        assert is_placeholder_value("Unknown Artist")
        assert is_placeholder_value(" n/a ")
        assert not is_placeholder_value("The Beatles")

    def test_generic_dir_name_detection(self):
        assert is_generic_dir_name("input")
        assert is_generic_dir_name(" Music ")
        assert not is_generic_dir_name("Beatles")


class TestCheckExternalTools:
    def test_all_present(self):
        with patch("music_importer.utils.shutil.which", return_value="/usr/bin/ffmpeg"):
            assert check_external_tools() == []

    def test_ffmpeg_missing(self):
        def fake_which(name):
            return None if name == "ffmpeg" else "/usr/bin/" + name

        with patch("music_importer.utils.shutil.which", side_effect=fake_which):
            missing = check_external_tools()
            assert "ffmpeg" in missing

    def test_all_missing(self):
        with patch("music_importer.utils.shutil.which", return_value=None):
            missing = check_external_tools()
            assert "ffmpeg" in missing
            assert "ffprobe" in missing


class TestCheckCueDependencies:
    def test_always_returns_empty(self, tmp_path):
        # CUE splitting uses ffmpeg directly; no additional tools are required.
        (tmp_path / "album.cue").touch()
        (tmp_path / "album.flac").touch()
        assert check_cue_dependencies(tmp_path) == []

    def test_no_cue_files(self, tmp_path):
        (tmp_path / "01.flac").touch()
        assert check_cue_dependencies(tmp_path) == []


class TestFindAudioFiles:
    def test_finds_flac_files(self, tmp_path):
        (tmp_path / "01.flac").touch()
        (tmp_path / "02.flac").touch()
        (tmp_path / "cover.jpg").touch()
        result = find_audio_files(tmp_path)
        assert len(result) == 2
        assert all(f.suffix == ".flac" for f in result)

    def test_finds_mixed_formats(self, tmp_path):
        (tmp_path / "track.flac").touch()
        (tmp_path / "track.mp3").touch()
        (tmp_path / "track.wav").touch()
        result = find_audio_files(tmp_path)
        assert len(result) == 3

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "track.FLAC").touch()
        (tmp_path / "track.Mp3").touch()
        result = find_audio_files(tmp_path)
        assert len(result) == 2

    def test_empty_dir(self, tmp_path):
        assert find_audio_files(tmp_path) == []


class TestFindCueFiles:
    def test_finds_cue(self, tmp_path):
        (tmp_path / "album.cue").touch()
        result = find_cue_files(tmp_path)
        assert len(result) == 1

    def test_case_insensitive_dedup(self, tmp_path):
        # On case-insensitive FS, these may be the same file
        (tmp_path / "album.cue").touch()
        # On case-sensitive FS, create the uppercase variant
        with contextlib.suppress(FileExistsError):
            (tmp_path / "album.CUE").touch()
        result = find_cue_files(tmp_path)
        # Should deduplicate on case-insensitive key
        assert len(result) == 1

    def test_no_cue_files(self, tmp_path):
        (tmp_path / "track.flac").touch()
        assert find_cue_files(tmp_path) == []


class TestDetectDiscSubdirs:
    def test_detects_cd_pattern(self, tmp_path):
        cd1 = tmp_path / "CD1"
        cd2 = tmp_path / "CD2"
        cd1.mkdir()
        cd2.mkdir()
        (cd1 / "01.flac").touch()
        (cd2 / "01.flac").touch()
        result = detect_disc_subdirs(tmp_path)
        assert result is not None
        assert len(result) == 2
        assert result[0] == (1, cd1)
        assert result[1] == (2, cd2)

    def test_detects_disc_pattern(self, tmp_path):
        d1 = tmp_path / "Disc 1"
        d2 = tmp_path / "Disc 2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "01.flac").touch()
        (d2 / "01.flac").touch()
        result = detect_disc_subdirs(tmp_path)
        assert result is not None
        assert len(result) == 2

    def test_returns_none_for_non_disc_dirs(self, tmp_path):
        d = tmp_path / "Bonus"
        d.mkdir()
        (d / "01.flac").touch()
        assert detect_disc_subdirs(tmp_path) is None

    def test_ignores_empty_disc_dirs(self, tmp_path):
        cd1 = tmp_path / "CD1"
        cd1.mkdir()
        # No audio files in CD1
        assert detect_disc_subdirs(tmp_path) is None

    def test_no_subdirs(self, tmp_path):
        (tmp_path / "01.flac").touch()
        assert detect_disc_subdirs(tmp_path) is None


class TestHasAudioSubdirs:
    def test_finds_subdirs_with_audio(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "track.flac").touch()
        result = has_audio_subdirs(tmp_path)
        assert len(result) == 1
        assert result[0] == sub

    def test_ignores_subdirs_without_audio(self, tmp_path):
        sub = tmp_path / "art"
        sub.mkdir()
        (sub / "cover.jpg").touch()
        assert has_audio_subdirs(tmp_path) == []
