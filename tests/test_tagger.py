from music_importer.tagger import read_source_tags


class TestReadSourceTags:
    def test_nonexistent_file(self, tmp_path):
        result = read_source_tags(tmp_path / "nonexistent.flac")
        assert result["title"] == ""
        assert result["track"] == 0

    def test_non_audio_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not audio")
        result = read_source_tags(f)
        assert result["title"] == ""

    def test_returns_all_expected_keys(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not audio")
        result = read_source_tags(f)
        expected_keys = {
            "title",
            "artist",
            "album",
            "albumartist",
            "date",
            "genre",
            "track",
            "total_tracks",
            "disc",
            "total_discs",
        }
        assert set(result.keys()) == expected_keys
