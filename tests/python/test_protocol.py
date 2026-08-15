"""Tests for native-messaging framing and request validation."""

from __future__ import annotations

import io
import json
import struct
import unittest

from native_host.errors import ErrorCode, HostError
from native_host.protocol import (
    MAX_MESSAGE_BYTES,
    MessageWriter,
    encode_message,
    read_message,
    redact_text,
    validate_request,
)


def request(action: str = "ping", payload: dict | None = None) -> dict:
    return {
        "version": 2,
        "requestId": "request-1",
        "action": action,
        "payload": payload or {},
    }


class ProtocolTest(unittest.TestCase):
    def test_round_trip_framing_uses_little_endian_length(self) -> None:
        encoded = encode_message(request())
        declared = struct.unpack("<I", encoded[:4])[0]
        self.assertEqual(declared, len(encoded) - 4)
        self.assertEqual(read_message(io.BytesIO(encoded)), request())

    def test_clean_eof_returns_none(self) -> None:
        self.assertIsNone(read_message(io.BytesIO()))

    def test_partial_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(HostError, "declared length"):
            read_message(io.BytesIO(struct.pack("<I", 20) + b"{}"))

    def test_oversized_message_is_rejected_before_reading_payload(self) -> None:
        stream = io.BytesIO(struct.pack("<I", MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(HostError) as caught:
            read_message(stream)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_MESSAGE)

    def test_unknown_fields_are_rejected(self) -> None:
        value = {**request(), "extra": True}
        with self.assertRaises(HostError):
            validate_request(value)

    def test_unsupported_version_has_stable_code(self) -> None:
        value = {**request(), "version": 1}
        with self.assertRaises(HostError) as caught:
            validate_request(value)
        self.assertEqual(caught.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_probe_payload_is_bounded(self) -> None:
        value = request(
            "probe",
            {
                "lessonUrl": "https://school.eduonline.io/learn/example/theory",
                "candidates": ["https://v.accelsite.io/v/ExamplePlayerId123456"],
            },
        )
        self.assertEqual(validate_request(value)["payload"], value["payload"])

    def test_writer_flushes_one_complete_frame(self) -> None:
        class FlushBuffer(io.BytesIO):
            flushed = False

            def flush(self) -> None:
                self.flushed = True

        stream = FlushBuffer()
        MessageWriter(stream).send({"ok": True})
        self.assertTrue(stream.flushed)
        size = struct.unpack("<I", stream.getvalue()[:4])[0]
        self.assertEqual(json.loads(stream.getvalue()[4 : 4 + size]), {"ok": True})

    def test_redaction_removes_url_details_and_tokens(self) -> None:
        result = redact_text(
            "GET https://example.com/private?id=secret Authorization: bearer-secret kcd=value"
        )
        self.assertNotIn("private", result)
        self.assertNotIn("secret", result)
        self.assertNotIn("value", result)
        self.assertIn("example.com", result)


if __name__ == "__main__":
    unittest.main()
