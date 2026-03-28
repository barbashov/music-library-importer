from __future__ import annotations

import time
import urllib.request

import musicbrainzngs as mb
from rich.console import Console

from music_importer.config import (
    MB_APP_NAME,
    MB_APP_VERSION,
    MB_CONTACT_EMAIL,
    MB_RATE_LIMIT_SECONDS,
)
from music_importer.models import ReleaseInfo, TrackInfo


class MusicBrainzClient:
    def __init__(self, console: Console | None = None):
        email = MB_CONTACT_EMAIL
        if not email:
            email = "anonymous@example.com"
            if console:
                console.print(
                    "[yellow]Warning:[/yellow] Set MUSIC_IMPORTER_EMAIL env var "
                    "for MusicBrainz API compliance."
                )
        mb.set_useragent(MB_APP_NAME, MB_APP_VERSION, email)
        self._last_request: float = 0.0
        self._console = console

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MB_RATE_LIMIT_SECONDS:
            time.sleep(MB_RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def search_releases(self, artist: str, album: str, limit: int = 5) -> list[dict]:
        """Search MB for releases, return list of matches."""
        self._rate_limit()
        try:
            result = mb.search_releases(artist=artist, release=album, limit=limit)
            releases: list[dict] = result.get("release-list", [])
            return releases
        except mb.WebServiceError as e:
            if self._console:
                self._console.print(f"[red]MusicBrainz search error:[/red] {e}")
            return []

    def search_release(self, artist: str, album: str) -> dict | None:
        """Search MB for a release, return best match or None."""
        releases = self.search_releases(artist, album)
        if not releases:
            return None
        # Prefer releases with complete track listings
        for r in releases:
            if r.get("medium-list"):
                return r
        return releases[0]

    def get_release_details(self, release_id: str) -> ReleaseInfo | None:
        """Fetch full release details and build ReleaseInfo."""
        self._rate_limit()
        try:
            result = mb.get_release_by_id(
                release_id,
                includes=["recordings", "artists", "labels", "release-groups", "media"],
            )
            release = result.get("release")
            if not release:
                return None
        except mb.WebServiceError as e:
            if self._console:
                self._console.print(f"[red]MusicBrainz fetch error:[/red] {e}")
            return None

        # Extract release-group ID for cover art fallback
        release_group = release.get("release-group", {})
        release_group_id = release_group.get("id")

        artist = release.get("artist-credit-phrase", "")
        date = release.get("date", "")
        year = date[:4] if date else ""

        # Extract genre from tags if available
        genre = ""
        tag_list = release.get("tag-list", [])
        if not tag_list:
            tag_list = release_group.get("tag-list", [])
        if tag_list:
            # Pick the tag with highest count
            best = max(tag_list, key=lambda t: int(t.get("count", 0)))
            genre = best.get("name", "")

        tracks = self._build_track_map(release)

        return ReleaseInfo(
            release_id=release_id,
            release_group_id=release_group_id,
            title=release.get("title", ""),
            artist=artist,
            date=date,
            year=year,
            genre=genre,
            tracks=tracks,
        )

    def get_cover_art(self, release_id: str, release_group_id: str | None = None) -> bytes | None:
        """Fetch cover art. Try release first, then release-group as fallback."""
        url = f"https://coverartarchive.org/release/{release_id}/front-500"
        data = self._fetch_cover(url)
        if data:
            return data

        # Fallback to release-group cover art
        if release_group_id:
            rg_url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
            return self._fetch_cover(rg_url)

        return None

    def _fetch_cover(self, url: str) -> bytes | None:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"{MB_APP_NAME}/{MB_APP_VERSION}"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data: bytes = r.read()
                return data
        except Exception:
            return None

    def _build_track_map(self, release: dict) -> dict[int, TrackInfo]:
        """Build flat {global_track_number: TrackInfo} across all discs."""
        track_map: dict[int, TrackInfo] = {}
        global_n = 1
        media_list = release.get("medium-list", [])
        total_discs = len(media_list)

        for medium in media_list:
            disc_num = int(medium.get("position", 1))
            tracks = medium.get("track-list", [])
            total_tracks = len(tracks)
            for t in tracks:
                rec = t.get("recording", {})
                artist_credit = rec.get("artist-credit", [])
                artist = artist_credit[0].get("artist", {}).get("name", "") if artist_credit else ""
                track_map[global_n] = TrackInfo(
                    title=rec.get("title", t.get("title", "")),
                    artist=artist,
                    track_number=int(t.get("position", global_n)),
                    total_tracks=total_tracks,
                    disc_number=disc_num,
                    total_discs=total_discs,
                )
                global_n += 1
        return track_map
