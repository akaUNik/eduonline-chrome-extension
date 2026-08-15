"""Structured errors exposed by the native host protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """Stable error codes shared with the Chrome extension."""

    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    UNSUPPORTED_PAGE = "UNSUPPORTED_PAGE"
    INVALID_PLAYER_URL = "INVALID_PLAYER_URL"
    INVALID_PROVIDER_CONFIG = "INVALID_PROVIDER_CONFIG"
    INVALID_MANIFEST_URL = "INVALID_MANIFEST_URL"
    NO_FORMATS = "NO_FORMATS"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    DRM_UNSUPPORTED = "DRM_UNSUPPORTED"
    NATIVE_HOST_UNAVAILABLE = "NATIVE_HOST_UNAVAILABLE"
    YTDLP_NOT_FOUND = "YTDLP_NOT_FOUND"
    FFMPEG_NOT_FOUND = "FFMPEG_NOT_FOUND"
    NETWORK_ERROR = "NETWORK_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    DOWNLOAD_TIMEOUT = "DOWNLOAD_TIMEOUT"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    BUSY = "BUSY"
    PROBE_EXPIRED = "PROBE_EXPIRED"
    INVALID_FORMAT = "INVALID_FORMAT"


@dataclass
class HostError(Exception):
    """Expected host failure with a safe user-facing message."""

    code: ErrorCode
    public_message: str
    debug_message: Optional[str] = None

    def __str__(self) -> str:
        """Return only the public message to avoid accidental secret logging."""
        return self.public_message
