from music_importer.debug import preview_object, summarize_binary, truncate_text


class TestDebugHelpers:
    def test_truncate_text_short(self):
        assert truncate_text("abc", limit=10) == "abc"

    def test_truncate_text_long(self):
        out = truncate_text("abcdef", limit=3)
        assert out.startswith("abc")
        assert "truncated" in out

    def test_preview_object(self):
        out = preview_object({"k": "v"})
        assert '"k": "v"' in out

    def test_summarize_binary(self):
        out = summarize_binary(b"\x89PNG fake cover", content_type="image/png")
        assert "type=image/png" in out
        assert "bytes=15" in out
        assert "sha256=" in out
