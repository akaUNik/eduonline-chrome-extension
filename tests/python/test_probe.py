"""Tests for metadata probing, normalization, and opaque probe tokens."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from native_host.errors import ErrorCode, HostError
from native_host.probe import (
    FormatChoice,
    ProbeCache,
    ProbeService,
    YtDlpProbeRunner,
    normalize_formats,
)
from native_host.provider import PlayerConfig


FIXTURES = Path(__file__).parents[1] / "fixtures"
MANIFEST = "https://kinescope.io/ExampleStreamId123456/master.m3u8"


class FormatNormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = json.loads((FIXTURES / "kinescope_probe.json").read_text(encoding="utf-8"))

    def test_normalizes_descending_video_and_audio_choices(self) -> None:
        choices = normalize_formats(self.metadata)

        self.assertEqual(
            [choice.choice_id for choice in choices],
            ["best", "video-720", "video-480", "video-360", "audio-only"],
        )
        self.assertEqual(choices[0].selector, "1357+audio_mp4a-English")
        self.assertEqual(choices[-1].selector, "audio_mp4a-English")

    def test_reports_drm_and_missing_formats(self) -> None:
        cases = [
            ({"formats": [{"format_id": "drm", "has_drm": True}]}, ErrorCode.DRM_UNSUPPORTED),
            ({"formats": []}, ErrorCode.NO_FORMATS),
        ]
        for metadata, code in cases:
            with self.subTest(code=code), self.assertRaises(HostError) as caught:
                normalize_formats(metadata)
            self.assertEqual(caught.exception.code, code)


class ProbeRunnerTest(unittest.TestCase):
    def test_uses_fixed_headers_without_shell_or_browser_cookies(self) -> None:
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, '{"formats": []}', "")

        runner = YtDlpProbeRunner(executable="/fake/yt-dlp", run_command=fake_run)
        result = runner.probe(MANIFEST)

        self.assertEqual(result, {"formats": []})
        command, kwargs = calls[0]
        self.assertIn("Origin:https://v.accelsite.io", command)
        self.assertIn("https://v.accelsite.io/", command)
        self.assertIn("--ignore-config", command)
        self.assertNotIn("--cookies-from-browser", command)
        self.assertIs(kwargs["shell"], False)

    def test_categorizes_403_without_exposing_url(self) -> None:
        secret_url = f"{MANIFEST}?secret=do-not-expose"

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", f"HTTP Error 403: {secret_url}")

        runner = YtDlpProbeRunner(executable="/fake/yt-dlp", run_command=fake_run)
        with self.assertRaises(HostError) as caught:
            runner.probe(MANIFEST)
        self.assertEqual(caught.exception.code, ErrorCode.AUTHORIZATION_REQUIRED)
        self.assertNotIn("secret", caught.exception.public_message)


class ProbeCacheTest(unittest.TestCase):
    def test_resolves_choice_and_rejects_tampering_and_expiry(self) -> None:
        now = [100.0]
        cache = ProbeCache(ttl_seconds=5, clock=lambda: now[0])
        choice = FormatChoice("video-720", "720p", "1357+audio", 720, False)
        token = cache.add(manifest_url=MANIFEST, media_id="stream-id", title="Title", choices=[choice])

        record, resolved = cache.resolve(token, "video-720")
        self.assertEqual(record.manifest_url, MANIFEST)
        self.assertEqual(resolved.selector, "1357+audio")
        with self.assertRaises(HostError) as caught:
            cache.resolve(token, "video-1080")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_FORMAT)
        now[0] = 106.0
        with self.assertRaises(HostError) as caught:
            cache.resolve(token, "video-720")
        self.assertEqual(caught.exception.code, ErrorCode.PROBE_EXPIRED)


class _FakeHttpClient:
    def fetch_player(self, url: str, lesson_url: str) -> PlayerConfig:
        return PlayerConfig(
            video_id="ExamplePlayerId123456",
            manifest_url=MANIFEST,
            title="Provider title",
            duration=2092,
            poster_url=None,
        )

    def preflight_manifest(self, url: str) -> str:
        return "#EXTM3U\n"


class _FakeRunner:
    def __init__(self, metadata):
        self.metadata = metadata

    def probe(self, manifest_url: str):
        return self.metadata


class ProbeServiceTest(unittest.TestCase):
    def test_returns_only_safe_metadata_and_caches_private_selector(self) -> None:
        metadata = json.loads((FIXTURES / "kinescope_probe.json").read_text(encoding="utf-8"))
        cache = ProbeCache()
        service = ProbeService(_FakeHttpClient(), _FakeRunner(metadata), cache)

        result = service.probe(
            "https://school.eduonline.io/learn/ExampleLesson/theory",
            ["https://v.accelsite.io/v/ExamplePlayerId123456"],
        )

        self.assertEqual(len(result["videos"]), 1)
        video = result["videos"][0]
        self.assertEqual(video["title"], "Provider title")
        self.assertNotIn("manifestUrl", video)
        self.assertNotIn("selector", video["formats"][0])
        record, choice = cache.resolve(video["probeToken"], "video-720")
        self.assertEqual(record.media_id, "ExampleStreamId123456")
        self.assertEqual(choice.selector, "1357+audio_mp4a-English")

    def test_preserves_order_deduplicates_and_returns_partial_success(self) -> None:
        metadata = json.loads((FIXTURES / "kinescope_probe.json").read_text(encoding="utf-8"))
        first_manifest = "https://kinescope.io/ExampleStreamOne123/master.m3u8"
        second_manifest = "https://kinescope.io/ExampleStreamTwo456/master.m3u8"
        candidates = [
            "https://v.accelsite.io/v/ExamplePlayerOne123",
            "https://v.accelsite.io/v/ExamplePlayerOne123?muted=true",
            "https://v.accelsite.io/v/ExampleBrokenPlayer789",
            "https://v.accelsite.io/v/ExamplePlayerTwo456",
            "https://v.accelsite.io/v/ExampleDuplicateMedia999",
        ]

        class MultiHttpClient:
            def __init__(self) -> None:
                self.fetches = []
                self.preflights = []

            def fetch_player(inner_self, url: str, lesson_url: str) -> PlayerConfig:
                inner_self.fetches.append(url)
                if "Broken" in url:
                    raise HostError(ErrorCode.NETWORK_ERROR, "broken fixture")
                if "One" in url:
                    return PlayerConfig("player-one", first_manifest, "First", 100, None)
                return PlayerConfig("player-two", second_manifest, "Second", 200, None)

            def preflight_manifest(inner_self, url: str) -> str:
                inner_self.preflights.append(url)
                return "#EXTM3U\n"

        class RecordingRunner:
            def __init__(self) -> None:
                self.manifests = []

            def probe(inner_self, manifest_url: str):
                inner_self.manifests.append(manifest_url)
                return metadata

        http_client = MultiHttpClient()
        runner = RecordingRunner()
        cache = ProbeCache()
        result = ProbeService(http_client, runner, cache).probe(
            "https://school.eduonline.io/learn/ExampleLesson/theory",
            candidates,
        )

        self.assertEqual(
            [video["videoId"] for video in result["videos"]],
            ["ExampleStreamOne123", "ExampleStreamTwo456"],
        )
        self.assertEqual([video["title"] for video in result["videos"]], ["First", "Second"])
        self.assertEqual(runner.manifests, [first_manifest, second_manifest])
        self.assertEqual(http_client.preflights, [first_manifest, second_manifest])
        self.assertEqual(result["summary"]["candidateCount"], 5)
        self.assertEqual(result["summary"]["duplicatePlayerCount"], 1)
        self.assertEqual(result["summary"]["duplicateMediaCount"], 1)
        self.assertEqual(result["summary"]["failures"], {"NETWORK_ERROR": 1})
        self.assertEqual(result["summary"]["failureStages"], {"PLAYER_FETCH:NETWORK_ERROR": 1})
        self.assertEqual(len(set(video["probeToken"] for video in result["videos"])), 2)
        for video in result["videos"]:
            record, _choice = cache.resolve(video["probeToken"], "best")
            self.assertEqual(record.media_id, video["videoId"])

    def test_raises_last_categorized_error_when_every_candidate_fails(self) -> None:
        class FailingHttpClient:
            def fetch_player(self, url: str, lesson_url: str) -> PlayerConfig:
                raise HostError(ErrorCode.AUTHORIZATION_REQUIRED, "denied")

        service = ProbeService(FailingHttpClient(), _FakeRunner({}), ProbeCache())
        with self.assertRaises(HostError) as caught:
            service.probe(
                "https://school.eduonline.io/learn/ExampleLesson/theory",
                ["https://v.accelsite.io/v/ExamplePlayerId123456"],
            )
        self.assertEqual(caught.exception.code, ErrorCode.AUTHORIZATION_REQUIRED)


if __name__ == "__main__":
    unittest.main()
