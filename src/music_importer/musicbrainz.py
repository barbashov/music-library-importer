from __future__ import annotations

import logging
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import musicbrainzngs as mb
from rich.console import Console

from music_importer.config import (
    MB_APP_NAME,
    MB_APP_VERSION,
    MB_CONTACT_EMAIL,
    MB_RATE_LIMIT_SECONDS,
)
from music_importer.debug import preview_object, summarize_binary, truncate_text
from music_importer.models import ReleaseInfo, TrackInfo

logger = logging.getLogger(__name__)
_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)
_REISSUE_KEYWORDS = (
    "anniversary",
    "deluxe",
    "edition",
    "expanded",
    "reissue",
    "remaster",
    "super value",
)

try:
    import socks
except ImportError:
    socks = None  # type: ignore[assignment]


@dataclass(frozen=True)
class _SocksProxyConfig:
    proxy_type: int
    host: str
    port: int
    username: str | None
    password: str | None
    rdns: bool
    scheme: str
    source_env: str


@dataclass(frozen=True)
class ReleaseSelectionHints:
    expected_discs: int | None = None
    expected_tracks: int | None = None


def _get_https_proxy_from_env() -> tuple[str | None, str | None]:
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key)
        if value:
            return value, key
    return None, None


def _parse_socks_proxy(proxy_url: str, source_env: str) -> _SocksProxyConfig | None:
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if not scheme.startswith("socks"):
        return None
    if socks is None:
        raise ValueError("PySocks is not installed.")

    scheme_map = {
        "socks5": (socks.SOCKS5, False),
        "socks5h": (socks.SOCKS5, True),
        "socks4": (socks.SOCKS4, False),
        "socks4a": (socks.SOCKS4, True),
    }
    if scheme not in scheme_map:
        raise ValueError(
            f"unsupported SOCKS scheme '{scheme}'. Use socks5, socks5h, socks4, or socks4a."
        )
    if not parsed.hostname:
        raise ValueError("missing proxy host")
    if parsed.port is None:
        raise ValueError("missing proxy port")

    proxy_type, rdns = scheme_map[scheme]
    return _SocksProxyConfig(
        proxy_type=proxy_type,
        host=parsed.hostname,
        port=parsed.port,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        rdns=rdns,
        scheme=scheme,
        source_env=source_env,
    )


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        cause = getattr(current, "cause", None)
        if isinstance(cause, BaseException) and id(cause) not in seen:
            current = cause
            continue
        next_exc = current.__cause__ or current.__context__
        current = next_exc if isinstance(next_exc, BaseException) else None


def _is_timeout_error(exc: BaseException) -> bool:
    for item in _iter_exception_chain(exc):
        if isinstance(item, (TimeoutError, socket.timeout)):
            return True
        if "timed out" in str(item).lower():
            return True
    return False


def _error_summary(exc: BaseException) -> str:
    for item in reversed(list(_iter_exception_chain(exc))):
        msg = str(item).strip()
        if msg:
            return f"{item.__class__.__name__}: {msg}"
    return exc.__class__.__name__


def _safe_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _release_score(release: dict) -> int:
    return _safe_int(release.get("ext:score")) or 0


def _release_disc_count(release: dict) -> int | None:
    count = _safe_int(release.get("medium-count"))
    if count is not None:
        return count
    media = release.get("medium-list", [])
    return len(media) if media else None


def _release_track_count(release: dict) -> int | None:
    count = _safe_int(release.get("medium-track-count"))
    if count is not None:
        return count
    media = release.get("medium-list", [])
    total = 0
    saw_any = False
    for medium in media:
        track_count = _safe_int(medium.get("track-count"))
        if track_count is None:
            continue
        saw_any = True
        total += track_count
    return total if saw_any else None


def _release_date_key(release: dict) -> tuple[int, int, int, int]:
    date_value = str(release.get("date", "")).strip()
    if not date_value:
        return (1, 9999, 12, 31)
    parts = date_value.split("-")
    year = _safe_int(parts[0]) if parts else None
    month = _safe_int(parts[1]) if len(parts) > 1 else None
    day = _safe_int(parts[2]) if len(parts) > 2 else None
    if year is None:
        return (1, 9999, 12, 31)
    return (0, year, month if month is not None else 12, day if day is not None else 31)


def _release_format_rank(release: dict) -> int:
    media = release.get("medium-list", [])
    formats = [
        str(medium.get("format", "")).strip().lower()
        for medium in media
        if str(medium.get("format", "")).strip()
    ]
    if not formats:
        return 3

    has_vinyl = any("vinyl" in fmt for fmt in formats)
    has_non_vinyl = any("vinyl" not in fmt for fmt in formats)
    if has_vinyl and not has_non_vinyl:
        return 4
    if has_vinyl and has_non_vinyl:
        return 3
    if any("cd" in fmt for fmt in formats):
        return 0
    if any(fmt == "digital media" for fmt in formats):
        return 1
    return 2


def _reissue_penalty(release: dict) -> int:
    disambiguation = str(release.get("disambiguation", "")).casefold()
    if not disambiguation:
        return 0
    return sum(1 for keyword in _REISSUE_KEYWORDS if keyword in disambiguation)


@contextmanager
def _socks_proxy_context(config: _SocksProxyConfig) -> Iterator[None]:
    if socks is None:
        raise RuntimeError("PySocks is not installed.")
    original_socket = socket.socket
    previous_env = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        socks.setdefaultproxy(
            config.proxy_type,
            config.host,
            config.port,
            rdns=config.rdns,
            username=config.username,
            password=config.password,
        )
        socket.socket = socks.socksocket  # type: ignore[misc]
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socks.setdefaultproxy()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class MusicBrainzClient:
    def __init__(self, console: Console | None = None, http_timeout: float = 15.0):
        email = MB_CONTACT_EMAIL
        if not email:
            email = "anonymous@example.com"
            if console:
                console.print(
                    "[yellow]Warning:[/yellow] Set MUSIC_IMPORTER_EMAIL env var "
                    "for MusicBrainz API compliance."
                )
        mb.set_useragent(MB_APP_NAME, MB_APP_VERSION, email)
        logger.debug(
            "MusicBrainz user-agent configured app=%s version=%s timeout=%s",
            MB_APP_NAME,
            MB_APP_VERSION,
            http_timeout,
        )
        self._last_request: float = 0.0
        self._console = console
        self._http_timeout = http_timeout
        self._socks_proxy = self._resolve_socks_proxy()

    def _resolve_socks_proxy(self) -> _SocksProxyConfig | None:
        proxy_url, source_env = _get_https_proxy_from_env()
        if not proxy_url or not source_env:
            logger.debug("MusicBrainz proxy mode=direct")
            return None
        try:
            config = _parse_socks_proxy(proxy_url, source_env)
        except ValueError as exc:
            logger.warning("Ignoring SOCKS proxy config from %s: %s", source_env, exc)
            if self._console:
                self._console.print(f"[yellow]Proxy warning:[/yellow] ignoring {source_env}: {exc}")
            return None
        if config is None:
            logger.debug(
                "MusicBrainz proxy mode=urllib source=%s scheme=%s",
                source_env,
                urlparse(proxy_url).scheme.lower() or "unknown",
            )
            return None
        logger.debug(
            "MusicBrainz proxy mode=socks source=%s scheme=%s host=%s port=%d rdns=%s",
            config.source_env,
            config.scheme,
            config.host,
            config.port,
            config.rdns,
        )
        return config

    @contextmanager
    def _network_context(self) -> Iterator[None]:
        if self._socks_proxy is None:
            yield
            return
        with _socks_proxy_context(self._socks_proxy):
            yield

    @contextmanager
    def _mb_timeout_context(self) -> Iterator[None]:
        """Apply timeout to musicbrainzngs requests that use urllib open() defaults."""
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self._http_timeout)
        try:
            yield
        finally:
            socket.setdefaulttimeout(previous)

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MB_RATE_LIMIT_SECONDS:
            logger.debug("Rate limiting sleep_seconds=%.3f", MB_RATE_LIMIT_SECONDS - elapsed)
            time.sleep(MB_RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def search_releases(self, artist: str | None, album: str, limit: int = 5) -> list[dict]:
        """Search MB for releases, return list of matches."""
        self._rate_limit()
        logger.debug(
            "MusicBrainz search_releases request artist=%r album=%r limit=%d timeout=%s",
            artist,
            album,
            limit,
            self._http_timeout,
        )
        try:
            with self._network_context(), self._mb_timeout_context():
                params: dict[str, str | int] = {"release": album, "limit": limit}
                if artist:
                    params["artist"] = artist
                result = mb.search_releases(**params)
            releases: list[dict] = result.get("release-list", [])
            logger.debug(
                "MusicBrainz search_releases response count=%d payload=%s",
                len(releases),
                preview_object(result),
            )
            return releases
        except mb.WebServiceError as e:
            summary = _error_summary(e)
            timeout = _is_timeout_error(e)
            logger.debug(
                "MusicBrainz search_releases failed timeout=%s detail=%s",
                timeout,
                summary,
            )
            if self._console:
                if timeout:
                    self._console.print(
                        "[yellow]MusicBrainz timeout:[/yellow] "
                        f"search exceeded {self._http_timeout:.1f}s. "
                        "Continuing without MusicBrainz metadata."
                    )
                else:
                    self._console.print(f"[red]MusicBrainz search error:[/red] {summary}")
            return []

    def search_release(
        self,
        artist: str | None,
        album: str,
        hints: ReleaseSelectionHints | None = None,
    ) -> dict | None:
        """Search MB for a release, return best match or None."""
        releases = self.search_releases(artist, album, limit=15)
        if not releases:
            return None
        return self._select_best_release(releases, hints or ReleaseSelectionHints())

    def _select_best_release(self, releases: list[dict], hints: ReleaseSelectionHints) -> dict:
        top_score = max(_release_score(release) for release in releases)
        top_candidates = [release for release in releases if _release_score(release) == top_score]
        ranked = sorted(top_candidates, key=lambda release: self._release_sort_key(release, hints))
        selected = ranked[0]
        logger.debug(
            "MusicBrainz release selection top_score=%d candidates=%d expected_discs=%s "
            "expected_tracks=%s selected_release_id=%s",
            top_score,
            len(ranked),
            hints.expected_discs,
            hints.expected_tracks,
            selected.get("id"),
        )
        for idx, candidate in enumerate(ranked[:5], start=1):
            logger.debug(
                "MusicBrainz candidate rank=%d release_id=%s score=%d discs=%s tracks=%s "
                "format_rank=%d reissue_penalty=%d date=%s disambiguation=%r",
                idx,
                candidate.get("id"),
                _release_score(candidate),
                _release_disc_count(candidate),
                _release_track_count(candidate),
                _release_format_rank(candidate),
                _reissue_penalty(candidate),
                candidate.get("date", ""),
                candidate.get("disambiguation", ""),
            )
        return selected

    def _release_sort_key(
        self, release: dict, hints: ReleaseSelectionHints
    ) -> tuple[int, int, int, int, int, int, tuple[int, int, int, int], str]:
        release_discs = _release_disc_count(release)
        release_tracks = _release_track_count(release)
        disc_match, disc_delta = self._match_quality(release_discs, hints.expected_discs)
        track_match, track_delta = self._match_quality(release_tracks, hints.expected_tracks)
        return (
            0 if release.get("medium-list") else 1,
            disc_match,
            track_match,
            disc_delta,
            track_delta,
            _release_format_rank(release) * 10 + _reissue_penalty(release),
            _release_date_key(release),
            str(release.get("id", "")),
        )

    def _match_quality(self, actual: int | None, expected: int | None) -> tuple[int, int]:
        if expected is None:
            return (0, 0)
        if actual is None:
            return (1, 10_000)
        if actual == expected:
            return (0, 0)
        return (1, abs(actual - expected))

    def get_release_details(self, release_id: str) -> ReleaseInfo | None:
        """Fetch full release details and build ReleaseInfo."""
        self._rate_limit()
        logger.debug(
            "MusicBrainz get_release_by_id request release_id=%s timeout=%s",
            release_id,
            self._http_timeout,
        )
        try:
            with self._network_context(), self._mb_timeout_context():
                result = mb.get_release_by_id(
                    release_id,
                    includes=["recordings", "artists", "labels", "release-groups", "media"],
                )
            release = result.get("release")
            logger.debug(
                "MusicBrainz get_release_by_id response payload=%s",
                preview_object(result),
            )
            if not release:
                return None
        except mb.WebServiceError as e:
            summary = _error_summary(e)
            timeout = _is_timeout_error(e)
            logger.debug(
                "MusicBrainz get_release_by_id failed timeout=%s release_id=%s detail=%s",
                timeout,
                release_id,
                summary,
            )
            if self._console:
                if timeout:
                    self._console.print(
                        "[yellow]MusicBrainz timeout:[/yellow] "
                        f"fetch exceeded {self._http_timeout:.1f}s. "
                        "Continuing without MusicBrainz metadata."
                    )
                else:
                    self._console.print(f"[red]MusicBrainz fetch error:[/red] {summary}")
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
        logger.debug("Cover art request url=%s", url)
        data = self._fetch_cover(url)
        if data:
            return data

        # Fallback to release-group cover art
        if release_group_id:
            rg_url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
            logger.debug("Cover art fallback request url=%s", rg_url)
            return self._fetch_cover(rg_url)

        return None

    def _fetch_cover(self, url: str) -> bytes | None:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"{MB_APP_NAME}/{MB_APP_VERSION}"}
            )
            logger.debug("Cover art request timeout_seconds=%s", self._http_timeout)
            with (
                self._network_context(),
                urllib.request.urlopen(req, timeout=self._http_timeout) as r,
            ):
                data: bytes = r.read()
                content_type = r.headers.get("Content-Type")
                status = getattr(r, "status", None)
                logger.debug(
                    "Cover art response status=%s summary=%s",
                    status,
                    summarize_binary(data, content_type=content_type),
                )
                if content_type and content_type.startswith("text/"):
                    logger.debug(
                        "Cover art text body preview=%s",
                        truncate_text(data.decode(errors="replace")),
                    )
                return data
        except Exception as exc:
            timeout = isinstance(exc, urllib.error.URLError) and _is_timeout_error(exc)
            logger.debug(
                "Cover art fetch failed url=%s timeout=%s detail=%s",
                url,
                timeout or _is_timeout_error(exc),
                _error_summary(exc),
            )
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
