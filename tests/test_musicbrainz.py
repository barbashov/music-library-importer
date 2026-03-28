from unittest.mock import MagicMock, patch

from music_importer.musicbrainz import MusicBrainzClient

SAMPLE_RELEASE_LIST = {
    "release-list": [
        {
            "id": "release-001",
            "title": "Abbey Road",
            "medium-list": [{"position": "1", "track-list": []}],
        },
        {
            "id": "release-002",
            "title": "Abbey Road (Remaster)",
        },
    ]
}

SAMPLE_RELEASE_DETAILS = {
    "release": {
        "id": "release-001",
        "title": "Abbey Road",
        "artist-credit-phrase": "The Beatles",
        "date": "1969-09-26",
        "release-group": {"id": "rg-001"},
        "medium-list": [
            {
                "position": "1",
                "track-list": [
                    {
                        "position": "1",
                        "recording": {
                            "title": "Come Together",
                            "artist-credit": [{"artist": {"name": "The Beatles"}}],
                        },
                    },
                    {
                        "position": "2",
                        "recording": {
                            "title": "Something",
                            "artist-credit": [{"artist": {"name": "The Beatles"}}],
                        },
                    },
                ],
            }
        ],
    }
}

MULTI_DISC_RELEASE = {
    "release": {
        "id": "release-002",
        "title": "The Wall",
        "artist-credit-phrase": "Pink Floyd",
        "date": "1979-11-30",
        "release-group": {"id": "rg-002"},
        "medium-list": [
            {
                "position": "1",
                "track-list": [
                    {
                        "position": "1",
                        "recording": {
                            "title": "In the Flesh?",
                            "artist-credit": [{"artist": {"name": "Pink Floyd"}}],
                        },
                    },
                ],
            },
            {
                "position": "2",
                "track-list": [
                    {
                        "position": "1",
                        "recording": {
                            "title": "Hey You",
                            "artist-credit": [{"artist": {"name": "Pink Floyd"}}],
                        },
                    },
                ],
            },
        ],
    }
}


@patch("music_importer.musicbrainz.mb")
class TestMusicBrainzClient:
    def _make_client(self, mock_mb):
        mock_mb.WebServiceError = Exception
        return MusicBrainzClient(console=None)

    def test_search_release_returns_best_match(self, mock_mb):
        mock_mb.search_releases.return_value = SAMPLE_RELEASE_LIST
        client = self._make_client(mock_mb)
        result = client.search_release("The Beatles", "Abbey Road")
        assert result is not None
        assert result["id"] == "release-001"

    def test_search_release_returns_none_on_empty(self, mock_mb):
        mock_mb.search_releases.return_value = {"release-list": []}
        client = self._make_client(mock_mb)
        assert client.search_release("Nobody", "Nothing") is None

    def test_get_release_details_extracts_release_group_id(self, mock_mb):
        mock_mb.get_release_by_id.return_value = SAMPLE_RELEASE_DETAILS
        client = self._make_client(mock_mb)
        info = client.get_release_details("release-001")
        assert info is not None
        assert info.release_group_id == "rg-001"
        assert info.title == "Abbey Road"
        assert info.artist == "The Beatles"
        assert info.year == "1969"

    def test_get_release_details_builds_track_map(self, mock_mb):
        mock_mb.get_release_by_id.return_value = SAMPLE_RELEASE_DETAILS
        client = self._make_client(mock_mb)
        info = client.get_release_details("release-001")
        assert info is not None
        assert len(info.tracks) == 2
        assert info.tracks[1].title == "Come Together"
        assert info.tracks[2].title == "Something"
        assert info.tracks[1].track_number == 1
        assert info.tracks[2].track_number == 2

    def test_multi_disc_track_map(self, mock_mb):
        mock_mb.get_release_by_id.return_value = MULTI_DISC_RELEASE
        client = self._make_client(mock_mb)
        info = client.get_release_details("release-002")
        assert info is not None
        assert len(info.tracks) == 2
        assert info.tracks[1].disc_number == 1
        assert info.tracks[1].title == "In the Flesh?"
        assert info.tracks[2].disc_number == 2
        assert info.tracks[2].title == "Hey You"
        assert info.tracks[1].total_discs == 2
        assert info.tracks[2].total_discs == 2

    @patch("music_importer.musicbrainz.urllib.request.urlopen")
    def test_get_cover_art_uses_release_group_fallback(self, mock_urlopen, mock_mb):
        client = self._make_client(mock_mb)

        # First call (release URL) fails, second call (release-group URL) succeeds
        call_count = 0

        def side_effect(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("404")
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"\x89PNG fake cover"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        mock_urlopen.side_effect = side_effect

        result = client.get_cover_art("release-001", "rg-001")
        assert result == b"\x89PNG fake cover"
        assert call_count == 2
        for call in mock_urlopen.call_args_list:
            assert call.kwargs.get("timeout") == 15.0

    @patch("music_importer.musicbrainz.urllib.request.urlopen")
    def test_get_cover_art_no_release_group_returns_none(self, mock_urlopen, mock_mb):
        client = self._make_client(mock_mb)
        mock_urlopen.side_effect = Exception("404")

        result = client.get_cover_art("release-001", None)
        assert result is None

    @patch("music_importer.musicbrainz.socket.getdefaulttimeout", return_value=None)
    @patch("music_importer.musicbrainz.socket.setdefaulttimeout")
    def test_musicbrainz_request_applies_and_restores_socket_timeout(
        self, mock_setdefaulttimeout, mock_getdefaulttimeout, mock_mb
    ):
        mock_mb.search_releases.return_value = {"release-list": []}
        client = MusicBrainzClient(console=None, http_timeout=9.5)

        client.search_releases("A", "B")

        assert mock_setdefaulttimeout.call_count == 2
        assert mock_setdefaulttimeout.call_args_list[0].args[0] == 9.5
        assert mock_setdefaulttimeout.call_args_list[1].args[0] is None

    @patch("music_importer.musicbrainz.urllib.request.urlopen")
    def test_cover_art_uses_custom_timeout(self, mock_urlopen, mock_mb):
        client = MusicBrainzClient(console=None, http_timeout=7.0)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"\x89PNG fake cover"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers.get.return_value = "image/png"
        mock_urlopen.return_value = mock_resp

        result = client.get_cover_art("release-001", None)

        assert result == b"\x89PNG fake cover"
        assert mock_urlopen.call_count == 1
        assert mock_urlopen.call_args.kwargs.get("timeout") == 7.0

    def test_search_releases_returns_list(self, mock_mb):
        mock_mb.search_releases.return_value = SAMPLE_RELEASE_LIST
        client = self._make_client(mock_mb)
        results = client.search_releases("The Beatles", "Abbey Road")
        assert len(results) == 2

    def test_rate_limiting(self, mock_mb):
        """Verify that consecutive calls enforce rate limiting."""
        mock_mb.search_releases.return_value = {"release-list": []}
        client = self._make_client(mock_mb)

        # Make two calls — rate limiter should add delay
        client.search_release("A", "B")
        import time

        start = time.monotonic()
        client.search_release("C", "D")
        elapsed = time.monotonic() - start

        # Should have waited approximately MB_RATE_LIMIT_SECONDS
        assert elapsed >= 0.8  # Allow some tolerance
