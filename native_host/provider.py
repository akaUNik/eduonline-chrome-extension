"""Strict AccelSite/Kinescope provider URL and configuration handling."""

from __future__ import annotations

import ast
import html as html_module
import re
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener
from typing import Callable, Optional

from native_host.errors import ErrorCode, HostError


OPAQUE_ID_PATTERN = r"[A-Za-z0-9_-]{6,128}"
PLAYER_PATH_PATTERN = re.compile(rf"^/v/(?P<player_id>{OPAQUE_ID_PATTERN})$")
MANIFEST_PATH_PATTERN = re.compile(rf"^/(?P<stream_id>{OPAQUE_ID_PATTERN})/master\.m3u8$")
LESSON_PATH_PATTERN = re.compile(r"^/learn/[^/]+(?:/.*)?$")
PLAYER_QUERY_KEYS = frozenset({"showTitle", "showControls", "muted"})
BOOLEAN_VALUES = frozenset({"true", "false"})
PLAYER_HOST = "v.accelsite.io"
MANIFEST_HOST = "kinescope.io"
POSTER_HOST = "cdn.app.axl.tech"
PLAYER_ORIGIN = "https://v.accelsite.io"
PLAYER_REFERER = "https://v.accelsite.io/"
MAX_PROVIDER_RESPONSE_BYTES = 512 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 3
PLAYER_PARSE_ATTEMPTS = 3
PLAYER_PARSE_RETRY_DELAY_SECONDS = 0.4
_PLAYER_CONFIG_START = re.compile(r"\bnew\s+AccelPlayer\s*\(")
_DURATION_PATTERN = re.compile(r"\bdurationInSec\s*:\s*(?P<value>\d{1,8})\b")


@dataclass(frozen=True)
class ValidatedPlayerUrl:
    """Validated AccelSite player URL and its opaque ID."""

    url: str
    player_id: str


@dataclass(frozen=True)
class ValidatedManifestUrl:
    """Validated Kinescope HLS manifest URL and its opaque ID."""

    url: str
    stream_id: str


@dataclass(frozen=True)
class PlayerConfig:
    """Scalar configuration extracted safely from an AccelPlayer page."""

    video_id: str
    manifest_url: str
    title: Optional[str]
    duration: Optional[int]
    poster_url: Optional[str]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Expose redirects to the caller so every target can be revalidated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class BoundedHttpClient:
    """Fetch allowlisted provider text with size, timeout, and redirect bounds."""

    def __init__(
        self,
        opener: Optional[OpenerDirector] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
        player_parse_attempts: int = PLAYER_PARSE_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._opener = opener or build_opener(_NoRedirectHandler())
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._player_parse_attempts = max(1, player_parse_attempts)
        self._sleep = sleep

    def fetch_player(self, url: str, lesson_url: str) -> PlayerConfig:
        """Fetch and parse a validated AccelSite player page."""
        validated = validate_player_url(url)
        referer = validate_lesson_url(lesson_url)
        for attempt in range(self._player_parse_attempts):
            body = self._fetch_text(
                validated.url,
                validate_player_url,
                headers={"Referer": referer},
            )
            try:
                return parse_player_html(body)
            except HostError as exc:
                if exc.code != ErrorCode.INVALID_PROVIDER_CONFIG or attempt + 1 >= self._player_parse_attempts:
                    raise
                self._sleep(PLAYER_PARSE_RETRY_DELAY_SECONDS)
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "AccelPlayer configuration was not found.")

    def preflight_manifest(self, url: str) -> str:
        """Verify a Kinescope master manifest is reachable with fixed headers."""
        validated = validate_manifest_url(url)
        body = self._fetch_text(
            validated.url,
            validate_manifest_url,
            headers={"Origin": PLAYER_ORIGIN, "Referer": PLAYER_REFERER},
        )
        if not body.lstrip().startswith("#EXTM3U"):
            raise HostError(
                ErrorCode.INVALID_PROVIDER_CONFIG,
                "The Kinescope response is not a valid HLS manifest.",
            )
        return body

    def _fetch_text(self, url: str, validator, headers: dict[str, str]) -> str:  # noqa: ANN001
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            request = Request(
                current_url,
                headers={"User-Agent": "eduonline-video-downloader/0.1", **headers},
                method="GET",
            )
            try:
                response = self._opener.open(request, timeout=self._timeout_seconds)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                    if redirect_count >= MAX_REDIRECTS:
                        raise HostError(ErrorCode.NETWORK_ERROR, "Provider redirected too many times.") from exc
                    target = urljoin(current_url, exc.headers["Location"])
                    current_url = validate_redirect(current_url, target)
                    continue
                if exc.code in {401, 403}:
                    raise HostError(
                        ErrorCode.AUTHORIZATION_REQUIRED,
                        "The video provider denied access to this lesson.",
                    ) from exc
                raise HostError(ErrorCode.NETWORK_ERROR, "The video provider request failed.") from exc
            except (TimeoutError, URLError, OSError) as exc:
                raise HostError(ErrorCode.NETWORK_ERROR, "The video provider could not be reached.") from exc

            with response:
                return self._read_bounded_utf8(response)
        raise HostError(ErrorCode.NETWORK_ERROR, "Provider redirect handling failed.")

    def _read_bounded_utf8(self, response: HTTPResponse) -> str:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise HostError(ErrorCode.NETWORK_ERROR, "Provider response length is invalid.") from exc
            if declared < 0 or declared > self._max_bytes:
                raise HostError(ErrorCode.NETWORK_ERROR, "Provider response is too large.")
        body = response.read(self._max_bytes + 1)
        if len(body) > self._max_bytes:
            raise HostError(ErrorCode.NETWORK_ERROR, "Provider response is too large.")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "Provider response is not UTF-8 text.") from exc


def _parse_url(url: str, code: ErrorCode, label: str):
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise HostError(code, f"{label} URL is invalid.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise HostError(code, f"{label} URL is invalid.") from exc
    if parsed.username is not None or parsed.password is not None:
        raise HostError(code, f"{label} URL must not contain credentials.")
    if port is not None:
        raise HostError(code, f"{label} URL must use the default port.")
    if parsed.fragment:
        raise HostError(code, f"{label} URL must not contain a fragment.")
    if not parsed.hostname:
        raise HostError(code, f"{label} URL has no host.")
    return parsed


def validate_lesson_url(url: str) -> str:
    """Validate an eduonline lesson URL used to bind a probe to a tab."""
    parsed = _parse_url(url, ErrorCode.UNSUPPORTED_PAGE, "Lesson")
    host = parsed.hostname.lower()
    if parsed.scheme not in {"http", "https"}:
        raise HostError(ErrorCode.UNSUPPORTED_PAGE, "Lesson URL scheme is unsupported.")
    if host != "eduonline.io" and not host.endswith(".eduonline.io"):
        raise HostError(ErrorCode.UNSUPPORTED_PAGE, "Open an eduonline.io lesson first.")
    if not LESSON_PATH_PATTERN.fullmatch(parsed.path):
        raise HostError(ErrorCode.UNSUPPORTED_PAGE, "Open an eduonline.io lesson first.")
    return url


def validate_player_url(url: str) -> ValidatedPlayerUrl:
    """Validate the exact AccelSite player shape observed in lesson iframes."""
    parsed = _parse_url(url, ErrorCode.INVALID_PLAYER_URL, "Player")
    if parsed.scheme != "https" or parsed.hostname.lower() != PLAYER_HOST:
        raise HostError(ErrorCode.INVALID_PLAYER_URL, "Player URL is not an approved AccelSite player.")
    match = PLAYER_PATH_PATTERN.fullmatch(parsed.path)
    if match is None:
        raise HostError(ErrorCode.INVALID_PLAYER_URL, "Player URL path is unsupported.")
    if parsed.query:
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise HostError(ErrorCode.INVALID_PLAYER_URL, "Player URL query is invalid.") from exc
    else:
        query = {}
    if not set(query).issubset(PLAYER_QUERY_KEYS):
        raise HostError(ErrorCode.INVALID_PLAYER_URL, "Player URL query contains unsupported keys.")
    if any(len(values) != 1 or values[0] not in BOOLEAN_VALUES for values in query.values()):
        raise HostError(ErrorCode.INVALID_PLAYER_URL, "Player URL query values are invalid.")
    return ValidatedPlayerUrl(url=url, player_id=match.group("player_id"))


def validate_manifest_url(url: str) -> ValidatedManifestUrl:
    """Validate the exact Kinescope HLS manifest shape exposed by AccelPlayer."""
    parsed = _parse_url(url, ErrorCode.INVALID_MANIFEST_URL, "Manifest")
    if parsed.scheme != "https" or parsed.hostname.lower() != MANIFEST_HOST:
        raise HostError(ErrorCode.INVALID_MANIFEST_URL, "Manifest URL is not an approved Kinescope manifest.")
    if parsed.query:
        raise HostError(ErrorCode.INVALID_MANIFEST_URL, "Manifest URL must not contain a query.")
    match = MANIFEST_PATH_PATTERN.fullmatch(parsed.path)
    if match is None:
        raise HostError(ErrorCode.INVALID_MANIFEST_URL, "Manifest URL path is unsupported.")
    return ValidatedManifestUrl(url=url, stream_id=match.group("stream_id"))


def validate_poster_url(url: str) -> str:
    """Validate optional display-only poster metadata."""
    parsed = _parse_url(url, ErrorCode.INVALID_PROVIDER_CONFIG, "Poster")
    if parsed.scheme != "https" or parsed.hostname.lower() != POSTER_HOST:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "Poster URL host is unsupported.")
    if parsed.query or not parsed.path.startswith("/"):
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "Poster URL shape is unsupported.")
    return url


def validate_redirect(source_url: str, target_url: str) -> str:
    """Revalidate a redirect without allowing a provider boundary change."""
    source_host = urlsplit(source_url).hostname
    target_host = urlsplit(target_url).hostname
    if source_host == PLAYER_HOST:
        validated = validate_player_url(target_url).url
    elif source_host == MANIFEST_HOST:
        validated = validate_manifest_url(target_url).url
    else:
        raise HostError(ErrorCode.NETWORK_ERROR, "Redirect source is unsupported.")
    if target_host != source_host:
        raise HostError(ErrorCode.NETWORK_ERROR, "Cross-host redirects are not allowed.")
    return validated


def parse_player_html(source: str) -> PlayerConfig:
    """Extract bounded scalar fields without evaluating inline JavaScript."""
    if not isinstance(source, str) or not 1 <= len(source.encode("utf-8")) <= MAX_PROVIDER_RESPONSE_BYTES:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "Player configuration size is invalid.")
    match = _PLAYER_CONFIG_START.search(source)
    if match is None:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "AccelPlayer configuration was not found.")
    config_source = source[match.start() :]

    video_id = _extract_js_string(config_source, "videoId", required=True, maximum=128)
    if not re.fullmatch(OPAQUE_ID_PATTERN, video_id or ""):
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "AccelPlayer video identifier is invalid.")

    manifest_raw = _extract_js_string(config_source, "url", required=True, maximum=2048)
    manifest_url = validate_manifest_url(manifest_raw or "").url
    title = _extract_js_string(config_source, "title", required=False, maximum=300)
    poster_raw = _extract_js_string(config_source, "poster", required=False, maximum=2048)
    poster = validate_poster_url(poster_raw) if poster_raw else None

    duration_match = _DURATION_PATTERN.search(config_source)
    duration = int(duration_match.group("value")) if duration_match else None
    if duration is not None and not 0 <= duration <= 7 * 24 * 60 * 60:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "AccelPlayer duration is invalid.")

    return PlayerConfig(
        video_id=video_id or "",
        manifest_url=manifest_url,
        title=title,
        duration=duration,
        poster_url=poster,
    )


def _extract_js_string(source: str, key: str, *, required: bool, maximum: int) -> Optional[str]:
    pattern = re.compile(
        rf"\b{re.escape(key)}\s*:\s*(?P<quoted>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
        re.DOTALL,
    )
    match = pattern.search(source)
    if match is None:
        if required:
            raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, f"AccelPlayer {key} is missing.")
        return None
    raw = match.group("quoted")
    if len(raw) > maximum * 6 + 2:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, f"AccelPlayer {key} is too long.")
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, f"AccelPlayer {key} is malformed.") from exc
    if not isinstance(value, str):
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, f"AccelPlayer {key} must be text.")
    value = html_module.unescape(value).strip()
    if not value or len(value) > maximum:
        raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, f"AccelPlayer {key} length is invalid.")
    return value
