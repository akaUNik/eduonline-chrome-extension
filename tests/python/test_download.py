"""Integration-style tests for the single native download worker."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from native_host.download import DownloadManager
from native_host.errors import ErrorCode, HostError
from native_host.probe import FormatChoice, ProbeCache


MANIFEST = "https://kinescope.io/ExampleStreamId123456/master.m3u8"


def _make_executable(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _wait_for(manager: DownloadManager, state: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.status()
        if snapshot["state"] == state:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"state {state!r} was not reached; got {manager.status()!r}")


class DownloadManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.downloads = self.root / "Downloads"
        self.ffmpeg = _make_executable(self.root, "ffmpeg", "raise SystemExit(0)")
        self.cache = ProbeCache()
        self.video = FormatChoice("video-720", "720p", "1357+audio", 720, False)
        self.audio = FormatChoice("audio-only", "Audio only (MP3)", "audio", None, True)
        self.token = self.cache.add(
            manifest_url=MANIFEST,
            media_id="ExampleStreamId123456",
            title="Example / lesson",
            choices=[self.video, self.audio],
        )
        self.events = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manager(self, yt_dlp: str) -> DownloadManager:
        return DownloadManager(
            self.cache,
            lambda request_id, event, payload: self.events.append((request_id, event, payload)),
            yt_dlp=yt_dlp,
            ffmpeg=self.ffmpeg,
            downloads_dir=self.downloads,
        )

    def test_success_reports_accepted_progress_and_sanitized_filename(self) -> None:
        yt_dlp = _make_executable(
            self.root,
            "yt-dlp-success",
            'print("EDUONLINE_PROGRESS:1357:42.5%", flush=True)\n'
            'print("EDUONLINE_FILE:/tmp/Example lesson [ExampleStreamId123456].mp4", flush=True)',
        )
        manager = self._manager(yt_dlp)

        manager.start("request-1", self.token, "video-720")
        snapshot = _wait_for(manager, "complete")

        self.assertEqual(snapshot["filename"], "Example lesson [ExampleStreamId123456].mp4")
        self.assertEqual([event for _, event, _ in self.events], ["accepted", "progress", "progress", "complete"])
        self.assertEqual(self.events[2][2]["percent"], 19.125)

    def test_video_audio_and_merge_have_45_45_10_progress_ranges(self) -> None:
        yt_dlp = _make_executable(
            self.root,
            "yt-dlp-two-stream-progress",
            'print("EDUONLINE_PROGRESS:1357:20%", flush=True)\n'
            'print("EDUONLINE_PROGRESS:1357:100%", flush=True)\n'
            'print("EDUONLINE_PROGRESS:audio:20%", flush=True)\n'
            'print("EDUONLINE_PROGRESS:audio:100%", flush=True)\n'
            'print("EDUONLINE_MERGE:Merger:started", flush=True)\n'
            'print("EDUONLINE_MERGE:Merger:finished", flush=True)\n'
            'print("EDUONLINE_FILE:/tmp/Example lesson [ExampleStreamId123456].mp4", flush=True)',
        )
        manager = self._manager(yt_dlp)

        manager.start("request-1", self.token, "video-720")
        _wait_for(manager, "complete")

        progress = [payload["percent"] for _, event, payload in self.events if event == "progress"]
        self.assertEqual(progress, [0.0, 9.0, 45.0, 54.0, 90.0, 90.0, 100.0])
        merge_states = [payload["state"] for _, event, payload in self.events if event == "progress"]
        self.assertEqual(merge_states[-2:], ["merging", "merging"])

    def test_audio_only_progress_uses_full_range(self) -> None:
        yt_dlp = _make_executable(
            self.root,
            "yt-dlp-audio-progress",
            'print("EDUONLINE_PROGRESS:audio:42.5%", flush=True)\n'
            'print("EDUONLINE_FILE:/tmp/Example lesson [ExampleStreamId123456].mp3", flush=True)',
        )
        manager = self._manager(yt_dlp)

        manager.start("request-1", self.token, "audio-only")
        _wait_for(manager, "complete")

        progress = [payload["percent"] for _, event, payload in self.events if event == "progress"]
        self.assertEqual(progress, [0.0, 42.5])

    def test_command_is_fixed_collision_safe_and_audio_specific(self) -> None:
        manager = self._manager(self.ffmpeg)
        record, audio = self.cache.resolve(self.token, "audio-only")

        command = manager._build_command(record, audio)

        self.assertIn("--no-overwrites", command)
        self.assertIn("--progress", command)
        self.assertEqual(command[command.index("--progress-delta") + 1], "0.5")
        templates = [command[index + 1] for index, item in enumerate(command) if item == "--progress-template"]
        self.assertEqual(len(templates), 2)
        self.assertTrue(any(template.startswith("download:EDUONLINE_PROGRESS:") for template in templates))
        self.assertTrue(any(template.startswith("postprocess:EDUONLINE_MERGE:") for template in templates))
        self.assertIn("Origin:https://v.accelsite.io", command)
        self.assertIn("https://v.accelsite.io/", command)
        self.assertIn("--extract-audio", command)
        self.assertIn("mp3", command)
        output = command[command.index("--output") + 1]
        self.assertTrue(output.startswith(str(self.downloads.resolve())))
        self.assertIn("Example _ lesson [ExampleStreamId123456]", output)

    def test_rejects_second_download_and_shutdown_kills_process_group(self) -> None:
        yt_dlp = _make_executable(
            self.root,
            "yt-dlp-slow",
            'import time\nprint("EDUONLINE_PROGRESS:1357:1%", flush=True)\ntime.sleep(30)',
        )
        manager = self._manager(yt_dlp)
        manager.start("request-1", self.token, "video-720")
        _wait_for(manager, "downloading")

        with self.assertRaises(HostError) as caught:
            manager.start("request-2", self.token, "audio-only")
        self.assertEqual(caught.exception.code, ErrorCode.BUSY)
        manager.shutdown()
        self.assertIsNone(manager._process)

    def test_categorizes_provider_failure(self) -> None:
        yt_dlp = _make_executable(
            self.root,
            "yt-dlp-failure",
            'import sys\nprint("ERROR: HTTP Error 403: forbidden")\nsys.exit(1)',
        )
        manager = self._manager(yt_dlp)

        manager.start("request-1", self.token, "video-720")
        snapshot = _wait_for(manager, "error")

        self.assertEqual(snapshot["error"]["code"], ErrorCode.AUTHORIZATION_REQUIRED.value)
        self.assertEqual(self.events[-1][1], "error")


if __name__ == "__main__":
    unittest.main()
