"""Chrome native-messaging framing, validation, and safe diagnostics."""

from __future__ import annotations

import json
import re
import struct
import sys
import threading
from collections.abc import Mapping
from typing import Any, BinaryIO, Optional
from urllib.parse import urlsplit

from native_host.errors import ErrorCode, HostError


PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_URL_LENGTH = 2048
MAX_CANDIDATES = 8
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
CHOICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SUPPORTED_ACTIONS = frozenset({"ping", "probe", "download", "status"})
_URL_PATTERN = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|cookie|set-cookie|token|signature|sig|key|kcd)\s*[:=]\s*[^\s,;]+"
)


def _invalid(message: str) -> HostError:
    return HostError(ErrorCode.INVALID_MESSAGE, message)


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    """Read exactly length bytes or raise a structured framing error."""
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise _invalid("Native message ended before its declared length.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: BinaryIO) -> Optional[dict[str, Any]]:
    """Read and validate one length-prefixed request from Chrome."""
    raw_length = stream.read(4)
    if not raw_length:
        return None
    if len(raw_length) != 4:
        raise _invalid("Native message has an incomplete length prefix.")

    (length,) = struct.unpack("<I", raw_length)
    if length <= 0 or length > MAX_MESSAGE_BYTES:
        raise _invalid("Native message size is outside the allowed range.")

    raw_payload = _read_exact(stream, length)
    try:
        decoded = raw_payload.decode("utf-8")
        message = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("Native message is not valid UTF-8 JSON.") from exc
    return validate_request(message)


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Encode one host response with the native-messaging length prefix."""
    try:
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid("Host response is not JSON serializable.") from exc
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise _invalid("Host response size is outside the allowed range.")
    return struct.pack("<I", len(payload)) + payload


class MessageWriter:
    """Serialize framed writes from the host read loop and worker thread."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: Mapping[str, Any]) -> None:
        """Write and flush one complete framed message atomically."""
        encoded = encode_message(message)
        with self._lock:
            self._stream.write(encoded)
            self._stream.flush()


def validate_request(value: Any) -> dict[str, Any]:
    """Validate a protocol-v2 request and return a normalized shallow copy."""
    if not isinstance(value, dict):
        raise _invalid("Native request must be a JSON object.")
    allowed_fields = {"version", "requestId", "action", "payload"}
    if set(value) != allowed_fields:
        raise _invalid("Native request contains missing or unknown fields.")

    version = value.get("version")
    if version != PROTOCOL_VERSION or isinstance(version, bool):
        raise HostError(
            ErrorCode.UNSUPPORTED_VERSION,
            "The extension and native host protocol versions do not match.",
        )

    request_id = value.get("requestId")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise _invalid("Request identifier is invalid.")

    action = value.get("action")
    if not isinstance(action, str) or action not in SUPPORTED_ACTIONS:
        raise HostError(ErrorCode.UNSUPPORTED_ACTION, "Native request action is unsupported.")

    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise _invalid("Native request payload must be an object.")
    normalized_payload = _validate_action_payload(action, payload)
    return {
        "version": PROTOCOL_VERSION,
        "requestId": request_id,
        "action": action,
        "payload": normalized_payload,
    }


def _validate_action_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action in {"ping", "status"}:
        if payload:
            raise _invalid(f"{action} payload must be empty.")
        return {}

    if action == "probe":
        if set(payload) != {"lessonUrl", "candidates"}:
            raise _invalid("Probe payload contains missing or unknown fields.")
        lesson_url = _bounded_string(payload.get("lessonUrl"), "lessonUrl", MAX_URL_LENGTH)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
            raise _invalid("Probe candidates must be a non-empty bounded array.")
        normalized_candidates = [
            _bounded_string(candidate, "candidate", MAX_URL_LENGTH) for candidate in candidates
        ]
        return {"lessonUrl": lesson_url, "candidates": normalized_candidates}

    if set(payload) != {"probeToken", "choiceId"}:
        raise _invalid("Download payload contains missing or unknown fields.")
    token = payload.get("probeToken")
    choice_id = payload.get("choiceId")
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise _invalid("Probe token is invalid.")
    if not isinstance(choice_id, str) or not CHOICE_ID_PATTERN.fullmatch(choice_id):
        raise _invalid("Format choice identifier is invalid.")
    return {"probeToken": token, "choiceId": choice_id}


def _bounded_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise _invalid(f"{name} must be a non-empty string up to {maximum} characters.")
    return value


def response_message(request_id: str, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a successful or progress host response."""
    return {
        "version": PROTOCOL_VERSION,
        "requestId": request_id,
        "event": event,
        "payload": dict(payload),
    }


def error_message(request_id: str, error: HostError) -> dict[str, Any]:
    """Build a structured error response without debug-only context."""
    return {
        "version": PROTOCOL_VERSION,
        "requestId": request_id,
        "event": "error",
        "error": {"code": error.code.value, "message": error.public_message},
    }


def redact_text(value: str) -> str:
    """Remove URL paths, queries, and credential-like fields from diagnostics."""
    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return "[REDACTED_URL]"
        if not parsed.hostname:
            return "[REDACTED_URL]"
        return f"{parsed.scheme}://{parsed.hostname}/[redacted]"

    without_urls = _URL_PATTERN.sub(redact_url, value)
    return _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", without_urls)


def log_event(event: str, **fields: object) -> None:
    """Write a compact redacted diagnostic event to stderr only."""
    safe_fields = {key: redact_text(str(value)) for key, value in fields.items()}
    record = {"event": event, **safe_fields}
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)
