"""AccelSite player probing, HLS format normalization, and probe tokens."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from native_host.errors import ErrorCode, HostError
from native_host.protocol import redact_text
from native_host.provider import (
    PLAYER_ORIGIN,
    PLAYER_REFERER,
    BoundedHttpClient,
    PlayerConfig,
    validate_lesson_url,
    validate_manifest_url,
    validate_player_url,
)


FORMAT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")
DEFAULT_PROBE_TTL_SECONDS = 15 * 60
MAX_PROBE_CACHE_ENTRIES = 32
PROBE_TIMEOUT_SECONDS = 45


@dataclass(frozen=True)
class FormatChoice:
    """Host-owned selector exposed through an opaque choice identifier."""

    choice_id: str
    label: str
    selector: str
    height: Optional[int]
    audio_only: bool

    def public_dict(self) -> dict[str, Any]:
        """Return the safe subset sent to the extension."""
        return {
            "choiceId": self.choice_id,
            "label": self.label,
            "height": self.height,
            "audioOnly": self.audio_only,
        }


@dataclass(frozen=True)
class ProbeRecord:
    """Private resolved media data retained only in native-host memory."""

    manifest_url: str
    media_id: str
    title: str
    choices: Mapping[str, FormatChoice]
    created_at: float


class ProbeCache:
    """Bounded in-memory cache for canonical URLs and format selectors."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_PROBE_TTL_SECONDS,
        max_entries: int = MAX_PROBE_CACHE_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._records: dict[str, ProbeRecord] = {}

    def add(
        self,
        *,
        manifest_url: str,
        media_id: str,
        title: str,
        choices: list[FormatChoice],
    ) -> str:
        """Store one probe and return a random token."""
        self._purge_expired()
        while len(self._records) >= self._max_entries:
            oldest = min(self._records, key=lambda token: self._records[token].created_at)
            del self._records[oldest]
        token = secrets.token_urlsafe(24)
        self._records[token] = ProbeRecord(
            manifest_url=manifest_url,
            media_id=media_id,
            title=title,
            choices={choice.choice_id: choice for choice in choices},
            created_at=self._clock(),
        )
        return token

    def resolve(self, token: str, choice_id: str) -> tuple[ProbeRecord, FormatChoice]:
        """Resolve a token/choice pair or reject expired and tampered input."""
        self._purge_expired()
        record = self._records.get(token)
        if record is None:
            raise HostError(ErrorCode.PROBE_EXPIRED, "Video metadata expired; reopen the popup.")
        choice = record.choices.get(choice_id)
        if choice is None:
            raise HostError(ErrorCode.INVALID_FORMAT, "The selected format is invalid.")
        return record, choice

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            token
            for token, record in self._records.items()
            if now - record.created_at > self._ttl_seconds
        ]
        for token in expired:
            del self._records[token]


class YtDlpProbeRunner:
    """Run a metadata-only yt-dlp probe with fixed provider headers."""

    def __init__(
        self,
        executable: Optional[str] = None,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable or shutil.which("yt-dlp") or ""
        self._run_command = run_command
        self._timeout_seconds = timeout_seconds

    def probe(self, manifest_url: str) -> dict[str, Any]:
        """Return parsed yt-dlp JSON without downloading media."""
        if not self._executable:
            raise HostError(ErrorCode.YTDLP_NOT_FOUND, "yt-dlp is not installed or executable.")
        command = [
            self._executable,
            "--ignore-config",
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "--add-header",
            f"Origin:{PLAYER_ORIGIN}",
            "--referer",
            PLAYER_REFERER,
            manifest_url,
        ]
        try:
            result = self._run_command(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise HostError(ErrorCode.YTDLP_NOT_FOUND, "yt-dlp is not installed or executable.") from exc
        except subprocess.TimeoutExpired as exc:
            raise HostError(ErrorCode.NETWORK_ERROR, "Video metadata request timed out.") from exc
        except OSError as exc:
            raise HostError(ErrorCode.NETWORK_ERROR, "yt-dlp could not be started.") from exc
        if result.returncode != 0:
            raise _categorize_probe_failure(result.stderr or result.stdout)
        try:
            metadata = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HostError(
                ErrorCode.INVALID_PROVIDER_CONFIG,
                "yt-dlp returned invalid video metadata.",
            ) from exc
        if not isinstance(metadata, dict):
            raise HostError(ErrorCode.INVALID_PROVIDER_CONFIG, "yt-dlp metadata has an invalid shape.")
        return metadata


class ProbeService:
    """Resolve a lesson iframe and return normalized, tokenized metadata."""

    def __init__(
        self,
        http_client: Optional[BoundedHttpClient] = None,
        runner: Optional[YtDlpProbeRunner] = None,
        cache: Optional[ProbeCache] = None,
    ) -> None:
        self.http_client = http_client or BoundedHttpClient()
        self.runner = runner or YtDlpProbeRunner()
        self.cache = cache or ProbeCache()

    def probe(self, lesson_url: str, candidates: list[str]) -> dict[str, Any]:
        """Probe every distinct supported player and return ordered videos."""
        validate_lesson_url(lesson_url)
        last_error: Optional[HostError] = None
        seen_player_ids: set[str] = set()
        seen_media_ids: set[str] = set()
        videos: list[dict[str, Any]] = []
        duplicate_players = 0
        duplicate_media = 0
        failure_counts: dict[str, int] = {}
        failure_stages: dict[str, int] = {}
        for candidate in candidates:
            stage = "PLAYER_URL"
            try:
                validated_player = validate_player_url(candidate)
                if validated_player.player_id in seen_player_ids:
                    duplicate_players += 1
                    continue
                seen_player_ids.add(validated_player.player_id)
                stage = "PLAYER_FETCH"
                config = self.http_client.fetch_player(validated_player.url, lesson_url)
                stage = "MANIFEST_VALIDATION"
                media_id = validate_manifest_url(config.manifest_url).stream_id
                if media_id in seen_media_ids:
                    duplicate_media += 1
                    continue
                seen_media_ids.add(media_id)
                stage = "MANIFEST_PREFLIGHT"
                self.http_client.preflight_manifest(config.manifest_url)
                stage = "YTDLP_PROBE"
                metadata = self.runner.probe(config.manifest_url)
                stage = "FORMAT_NORMALIZATION"
                choices = normalize_formats(metadata)
                title = _select_title(config, metadata)
                token = self.cache.add(
                    manifest_url=config.manifest_url,
                    media_id=media_id,
                    title=title,
                    choices=choices,
                )
                videos.append(
                    {
                        "videoId": media_id,
                        "probeToken": token,
                        "title": title,
                        "poster": config.poster_url,
                        "duration": config.duration or _positive_int(metadata.get("duration")),
                        "formats": [choice.public_dict() for choice in choices],
                    }
                )
            except HostError as exc:
                last_error = exc
                failure_counts[exc.code.value] = failure_counts.get(exc.code.value, 0) + 1
                stage_key = f"{stage}:{exc.code.value}"
                failure_stages[stage_key] = failure_stages.get(stage_key, 0) + 1
        if videos:
            return {
                "videos": videos,
                "summary": {
                    "candidateCount": len(candidates),
                    "uniquePlayerCount": len(seen_player_ids),
                    "duplicatePlayerCount": duplicate_players,
                    "duplicateMediaCount": duplicate_media,
                    "failures": failure_counts,
                    "failureStages": failure_stages,
                },
            }
        if last_error is not None:
            raise last_error
        raise HostError(ErrorCode.INVALID_PLAYER_URL, "No supported AccelSite player was found.")


def normalize_formats(metadata: Mapping[str, Any]) -> list[FormatChoice]:
    """Normalize yt-dlp formats into safe distinct user choices."""
    raw_formats = metadata.get("formats")
    if not isinstance(raw_formats, list):
        raise HostError(ErrorCode.NO_FORMATS, "The video has no supported formats.")

    video_by_height: dict[int, dict[str, Any]] = {}
    audio_formats: list[dict[str, Any]] = []
    drm_seen = False
    for raw in raw_formats:
        if not isinstance(raw, dict):
            continue
        drm_seen = drm_seen or bool(raw.get("has_drm"))
        if raw.get("has_drm"):
            continue
        format_id = raw.get("format_id")
        if not isinstance(format_id, str) or not FORMAT_ID_PATTERN.fullmatch(format_id):
            continue
        height = _positive_int(raw.get("height"))
        vcodec = str(raw.get("vcodec") or "none").lower()
        acodec = str(raw.get("acodec") or "none").lower()
        if height and vcodec != "none":
            existing = video_by_height.get(height)
            if existing is None or _bitrate(raw) > _bitrate(existing):
                video_by_height[height] = raw
        elif vcodec == "none" and (acodec != "none" or "audio" in format_id.lower()):
            audio_formats.append(raw)

    if not video_by_height and not audio_formats:
        code = ErrorCode.DRM_UNSUPPORTED if drm_seen else ErrorCode.NO_FORMATS
        message = (
            "This video is DRM-protected and cannot be downloaded."
            if drm_seen
            else "The video has no supported formats."
        )
        raise HostError(code, message)

    best_audio = max(audio_formats, key=_bitrate, default=None)
    audio_id = str(best_audio["format_id"]) if best_audio else None
    heights = sorted(video_by_height, reverse=True)
    choices: list[FormatChoice] = []
    if heights:
        best_video_id = str(video_by_height[heights[0]]["format_id"])
        choices.append(
            FormatChoice(
                choice_id="best",
                label=f"Best ({heights[0]}p)",
                selector=_combine_selector(best_video_id, audio_id),
                height=heights[0],
                audio_only=False,
            )
        )
        for height in heights:
            video_id = str(video_by_height[height]["format_id"])
            choices.append(
                FormatChoice(
                    choice_id=f"video-{height}",
                    label=f"{height}p",
                    selector=_combine_selector(video_id, audio_id),
                    height=height,
                    audio_only=False,
                )
            )
    if audio_id:
        choices.append(
            FormatChoice(
                choice_id="audio-only",
                label="Audio only (MP3)",
                selector=audio_id,
                height=None,
                audio_only=True,
            )
        )
    return choices


def _combine_selector(video_id: str, audio_id: Optional[str]) -> str:
    return f"{video_id}+{audio_id}" if audio_id else video_id


def _bitrate(raw: Mapping[str, Any]) -> float:
    for key in ("tbr", "abr", "vbr"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def _select_title(config: PlayerConfig, metadata: Mapping[str, Any]) -> str:
    if config.title:
        return config.title
    candidate = metadata.get("title")
    if isinstance(candidate, str) and candidate.strip() and candidate.strip().lower() != "master":
        return candidate.strip()[:300]
    return "Lesson video"


def _categorize_probe_failure(details: str) -> HostError:
    safe_details = redact_text(details)[:500]
    lowered = details.lower()
    if "drm" in lowered or "encrypted" in lowered:
        return HostError(
            ErrorCode.DRM_UNSUPPORTED,
            "This video is DRM-protected and cannot be downloaded.",
            safe_details,
        )
    if "http error 401" in lowered or "http error 403" in lowered or "forbidden" in lowered:
        return HostError(
            ErrorCode.AUTHORIZATION_REQUIRED,
            "The video provider denied access to this lesson.",
            safe_details,
        )
    if any(term in lowered for term in ("unable to download", "network", "timed out", "connection")):
        return HostError(ErrorCode.NETWORK_ERROR, "Video metadata could not be downloaded.", safe_details)
    return HostError(
        ErrorCode.INVALID_PROVIDER_CONFIG,
        "yt-dlp could not inspect the Kinescope manifest.",
        safe_details,
    )
