"""Tests for native-host dispatch and responsive status handling."""

from __future__ import annotations

import io
import json
import struct
import unittest

from native_host.host import NativeHost
from native_host.protocol import encode_message


def _request(request_id: str, action: str, payload: dict) -> dict:
    return {"version": 2, "requestId": request_id, "action": action, "payload": payload}


class _FakeProbeService:
    cache = object()

    def probe(self, lesson_url, candidates):
        return {"videos": [{"probeToken": "a" * 24, "title": "Lesson", "formats": []}]}


class _FakeDownloadManager:
    def __init__(self) -> None:
        self.emit = None
        self.started = []
        self.stopped = False

    def start(self, request_id, token, choice_id):
        self.started.append((request_id, token, choice_id))
        return {"state": "accepted"}

    def status(self):
        return {"state": "idle"}

    def shutdown(self):
        self.stopped = True


class NativeHostTest(unittest.TestCase):
    def test_ping_and_status_are_correlated_and_eof_shuts_down(self) -> None:
        input_stream = io.BytesIO(
            encode_message(_request("ping-1", "ping", {}))
            + encode_message(_request("status-1", "status", {}))
        )
        output_stream = io.BytesIO()
        manager = _FakeDownloadManager()
        host = NativeHost(input_stream, output_stream, _FakeProbeService(), manager)

        self.assertEqual(host.run(), 0)
        self.assertTrue(manager.stopped)
        output_stream.seek(0)
        first = _read_response(output_stream)
        second = _read_response(output_stream)
        self.assertEqual((first["requestId"], first["event"]), ("ping-1", "result"))
        self.assertEqual((second["requestId"], second["event"]), ("status-1", "status"))

    def test_malformed_framing_returns_structured_error(self) -> None:
        input_stream = io.BytesIO(struct.pack("<I", 4) + b"nope")
        output_stream = io.BytesIO()
        manager = _FakeDownloadManager()

        self.assertEqual(NativeHost(input_stream, output_stream, _FakeProbeService(), manager).run(), 2)
        output_stream.seek(0)
        response = _read_response(output_stream)
        self.assertEqual(response["requestId"], "host")
        self.assertEqual(response["event"], "error")
        self.assertEqual(response["error"]["code"], "INVALID_MESSAGE")


def _read_response(stream: io.BytesIO) -> dict:
    length_raw = stream.read(4)
    (length,) = struct.unpack("<I", length_raw)
    return json.loads(stream.read(length))


if __name__ == "__main__":
    unittest.main()
