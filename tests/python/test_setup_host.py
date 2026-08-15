"""Tests for isolated macOS native-host setup and diagnostics."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import setup_host


EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
REPO_ROOT = Path(__file__).parents[2]


def _executable(path: Path, output: str = "test-version") -> Path:
    path.write_text(f"#!/bin/sh\necho {output}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class SetupHostTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.yt_dlp = _executable(self.bin / "yt-dlp", "2026.01.01")
        self.ffmpeg = _executable(self.bin / "ffmpeg", "ffmpeg-test")
        self.chrome = self.root / "Google Chrome.app"
        self.chrome.mkdir()
        self.prerequisites = setup_host.Prerequisites(
            Path(sys.executable).resolve(),
            self.yt_dlp,
            self.ffmpeg,
            self.chrome,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preflight_is_read_only_and_reports_resolved_tools(self) -> None:
        before = list(self.home.rglob("*"))
        result = setup_host.preflight(
            home=self.home,
            chrome_path=self.chrome,
            which=lambda name: str(self.bin / name),
            system_name="Darwin",
            python_version=(3, 8, 0),
            python_executable=sys.executable,
        )

        self.assertEqual(result.yt_dlp, self.yt_dlp.resolve())
        self.assertEqual(list(self.home.rglob("*")), before)

    def test_invalid_extension_id_writes_nothing(self) -> None:
        with self.assertRaises(setup_host.SetupError):
            setup_host.install(
                "invalid-id",
                repo_root=REPO_ROOT,
                home=self.home,
                prerequisites=self.prerequisites,
            )
        self.assertEqual(list(self.home.rglob("*")), [])

    def test_install_is_idempotent_and_refreshes_moved_checkout(self) -> None:
        paths = setup_host.install(
            EXTENSION_ID,
            repo_root=REPO_ROOT,
            home=self.home,
            prerequisites=self.prerequisites,
        )
        first_manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        self.assertEqual(first_manifest["allowed_origins"], [f"chrome-extension://{EXTENSION_ID}/"])
        self.assertEqual(first_manifest["path"], str(paths.launcher.resolve()))
        self.assertTrue(os.access(paths.launcher, os.X_OK))

        moved = self.root / "moved-checkout"
        (moved / "native_host").mkdir(parents=True)
        (moved / "native_host" / "host.py").write_text("# fixture\n", encoding="utf-8")
        setup_host.install(
            EXTENSION_ID,
            repo_root=moved,
            home=self.home,
            prerequisites=self.prerequisites,
        )
        self.assertIn(str(moved.resolve()), paths.launcher.read_text(encoding="utf-8"))

    def test_diagnostics_ping_and_scoped_removal(self) -> None:
        paths = setup_host.install(
            EXTENSION_ID,
            repo_root=REPO_ROOT,
            home=self.home,
            prerequisites=self.prerequisites,
        )
        unrelated = paths.launcher.parent.parent / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PATH": f"{self.bin}:/usr/bin:/bin"}):
            report = setup_host.diagnose(home=self.home)
        self.assertEqual(report["origin"], f"chrome-extension://{EXTENSION_ID}/")

        removed = setup_host.remove(home=self.home)
        self.assertEqual(set(removed), {paths.manifest, paths.launcher})
        self.assertTrue(unrelated.exists())

    def test_partial_manifest_failure_restores_previous_launcher(self) -> None:
        paths = setup_host.install(
            EXTENSION_ID,
            repo_root=REPO_ROOT,
            home=self.home,
            prerequisites=self.prerequisites,
        )
        original = paths.launcher.read_bytes()
        real_write = setup_host._atomic_write
        calls = [0]

        def fail_second(path, content, mode):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("simulated")
            return real_write(path, content, mode)

        with mock.patch.object(setup_host, "_atomic_write", side_effect=fail_second):
            with self.assertRaises(setup_host.SetupError):
                setup_host.install(
                    EXTENSION_ID,
                    repo_root=REPO_ROOT,
                    home=self.home,
                    prerequisites=self.prerequisites,
                )
        self.assertEqual(paths.launcher.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
