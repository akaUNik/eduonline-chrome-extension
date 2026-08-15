"""Tests for the closed provider URL policy."""

from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from email.message import Message

from native_host.errors import ErrorCode, HostError
from native_host.provider import (
    MAX_PROVIDER_RESPONSE_BYTES,
    BoundedHttpClient,
    parse_player_html,
    validate_lesson_url,
    validate_manifest_url,
    validate_player_url,
    validate_poster_url,
    validate_redirect,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


class ProviderUrlTest(unittest.TestCase):
    def test_accepts_supported_lesson_and_player(self) -> None:
        lesson = "https://school.eduonline.io/learn/ExampleLesson/theory"
        player = "https://v.accelsite.io/v/ExamplePlayerId123456?showTitle=true&muted=false"
        self.assertEqual(validate_lesson_url(lesson), lesson)
        self.assertEqual(validate_player_url(player).player_id, "ExamplePlayerId123456")

    def test_accepts_verified_manifest_and_poster_shapes(self) -> None:
        manifest = "https://kinescope.io/ExampleStreamId123456/master.m3u8"
        poster = "https://cdn.app.axl.tech/example/images/poster.png"
        self.assertEqual(validate_manifest_url(manifest).stream_id, "ExampleStreamId123456")
        self.assertEqual(validate_poster_url(poster), poster)

    def test_rejects_player_credentials_port_fragment_and_unknown_query(self) -> None:
        invalid = [
            "https://user@v.accelsite.io/v/ExamplePlayerId123456",
            "https://v.accelsite.io:444/v/ExamplePlayerId123456",
            "https://v.accelsite.io/v/ExamplePlayerId123456#fragment",
            "https://v.accelsite.io/v/ExamplePlayerId123456?token=secret",
            "https://v.accelsite.io/v/ExamplePlayerId123456?muted=1",
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(HostError) as caught:
                validate_player_url(url)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_PLAYER_URL)

    def test_rejects_manifest_query_wrong_host_ip_and_path(self) -> None:
        invalid = [
            "https://kinescope.io/ExampleStreamId123456/master.m3u8?key=secret",
            "https://edge.kinescope.io/ExampleStreamId123456/master.m3u8",
            "https://127.0.0.1/ExampleStreamId123456/master.m3u8",
            "https://kinescope.io/ExampleStreamId123456/master.mpd",
        ]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(HostError) as caught:
                validate_manifest_url(url)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_MANIFEST_URL)

    def test_revalidates_redirect_target(self) -> None:
        source = "https://v.accelsite.io/v/ExamplePlayerId123456"
        target = "https://v.accelsite.io/v/OtherPlayerId123456"
        self.assertEqual(validate_redirect(source, target), target)
        with self.assertRaises(HostError):
            validate_redirect(source, "https://example.com/v/OtherPlayerId123456")


class PlayerConfigTest(unittest.TestCase):
    def test_parses_sanitized_accel_player_fixture(self) -> None:
        config = parse_player_html((FIXTURES / "accel_player.html").read_text(encoding="utf-8"))

        self.assertEqual(config.video_id, "ExamplePlayerId123456")
        self.assertEqual(
            config.manifest_url,
            "https://kinescope.io/ExampleStreamId123456/master.m3u8",
        )
        self.assertEqual(config.title, "Example lesson")
        self.assertEqual(config.duration, 2092)
        self.assertEqual(
            config.poster_url,
            "https://cdn.app.axl.tech/example/images/poster.png",
        )

    def test_rejects_missing_configuration_and_oversized_input(self) -> None:
        for source in ("<html></html>", "x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(source)), self.assertRaises(HostError) as caught:
                parse_player_html(source)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_PROVIDER_CONFIG)

    def test_rejects_manifest_outside_allowlist(self) -> None:
        source = """
        <script>new AccelPlayer({
          videoId: "ExamplePlayerId123456",
          url: "https://evil.example/ExampleStreamId123456/master.m3u8"
        });</script>
        """
        with self.assertRaises(HostError) as caught:
            parse_player_html(source)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_MANIFEST_URL)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = BytesIO(body)
        self.headers = Message()
        self.headers["Content-Length"] = str(len(body))

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class _FakeOpener:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class ProviderFetchTest(unittest.TestCase):
    def test_retries_incomplete_lazy_player_html_then_parses_configuration(self) -> None:
        valid = (FIXTURES / "accel_player.html").read_bytes()
        opener = _FakeOpener([
            _FakeResponse(b"<html><body>loading</body></html>"),
            _FakeResponse(valid),
        ])
        delays = []
        client = BoundedHttpClient(
            opener=opener,
            player_parse_attempts=2,
            sleep=delays.append,
        )

        lesson_url = "https://tenant.eduonline.io/learn/LessonId/theory"
        config = client.fetch_player(
            "https://v.accelsite.io/v/ExamplePlayerId123456",
            lesson_url,
        )

        self.assertEqual(config.title, "Example lesson")
        self.assertEqual(delays, [0.4])
        self.assertEqual(len(opener.requests), 2)
        for request, _timeout in opener.requests:
            self.assertEqual(request.get_header("Referer"), lesson_url)
            self.assertIsNone(request.get_header("Cookie"))

    def test_preflight_sends_only_fixed_provider_headers(self) -> None:
        opener = _FakeOpener([_FakeResponse(b"#EXTM3U\n")])
        client = BoundedHttpClient(opener=opener)

        client.preflight_manifest("https://kinescope.io/ExampleStreamId123456/master.m3u8")

        request, timeout = opener.requests[0]
        self.assertEqual(request.get_header("Origin"), "https://v.accelsite.io")
        self.assertEqual(request.get_header("Referer"), "https://v.accelsite.io/")
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(timeout, 15.0)

    def test_rejects_cross_host_redirect_and_403(self) -> None:
        redirect_headers = Message()
        redirect_headers["Location"] = "https://evil.example/ExampleStreamId123456/master.m3u8"
        redirect = HTTPError(MANIFEST_URL, 302, "Found", redirect_headers, BytesIO())
        forbidden = HTTPError(MANIFEST_URL, 403, "Forbidden", Message(), BytesIO())
        self.addCleanup(redirect.close)
        self.addCleanup(forbidden.close)
        for response, code in (
            (redirect, ErrorCode.INVALID_MANIFEST_URL),
            (forbidden, ErrorCode.AUTHORIZATION_REQUIRED),
        ):
            with self.subTest(code=code):
                client = BoundedHttpClient(opener=_FakeOpener([response]))
                with self.assertRaises(HostError) as caught:
                    client.preflight_manifest(MANIFEST_URL)
                self.assertEqual(caught.exception.code, code)


MANIFEST_URL = "https://kinescope.io/ExampleStreamId123456/master.m3u8"


if __name__ == "__main__":
    unittest.main()
