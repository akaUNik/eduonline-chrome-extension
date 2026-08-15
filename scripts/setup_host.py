#!/usr/bin/env python3
"""Install, diagnose, or remove the macOS Chrome native-messaging host."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


HOST_NAME = "io.eduonline.ytdlp"
EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
CHROME_APP = Path("/Applications/Google Chrome.app")
MINIMUM_PYTHON = (3, 8)


class SetupError(Exception):
    """Actionable setup failure safe to print to a terminal."""


@dataclass(frozen=True)
class InstallPaths:
    manifest: Path
    launcher: Path


@dataclass(frozen=True)
class Prerequisites:
    python: Path
    yt_dlp: Path
    ffmpeg: Path
    chrome: Path


def install_paths(home: Path) -> InstallPaths:
    support = home / "Library" / "Application Support"
    return InstallPaths(
        manifest=support / "Google" / "Chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json",
        launcher=support / "eduonline-video-downloader" / "native-host" / "launch.sh",
    )


def validate_extension_id(extension_id: str) -> str:
    if not EXTENSION_ID_PATTERN.fullmatch(extension_id or ""):
        raise SetupError("Chrome extension ID must contain exactly 32 letters from a through p.")
    return extension_id


def preflight(
    *,
    home: Path,
    chrome_path: Path = CHROME_APP,
    which: Callable[[str], Optional[str]] = shutil.which,
    system_name: Optional[str] = None,
    python_version: Optional[Sequence[int]] = None,
    python_executable: Optional[str] = None,
) -> Prerequisites:
    """Resolve every prerequisite without creating registration files."""
    if (system_name or platform.system()) != "Darwin":
        raise SetupError("This setup workflow currently supports macOS only.")
    version = tuple(python_version or sys.version_info[:3])
    if version < MINIMUM_PYTHON:
        raise SetupError("Python 3.8 or newer is required.")
    python = Path(python_executable or sys.executable).resolve()
    if not python.is_file() or not os.access(str(python), os.X_OK):
        raise SetupError("The current Python interpreter is not executable.")
    yt_dlp = which("yt-dlp")
    if not yt_dlp:
        raise SetupError("yt-dlp was not found. Install it with: brew install yt-dlp")
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        raise SetupError("ffmpeg was not found. Install it with: brew install ffmpeg")
    if not chrome_path.is_dir():
        raise SetupError("Google Chrome was not found in /Applications.")
    paths = install_paths(home)
    for target in (paths.manifest, paths.launcher):
        parent = _nearest_existing_parent(target.parent)
        if not os.access(str(parent), os.W_OK):
            raise SetupError(f"Setup cannot write beneath {parent}.")
    return Prerequisites(python, Path(yt_dlp).resolve(), Path(ffmpeg).resolve(), chrome_path.resolve())


def install(
    extension_id: str,
    *,
    repo_root: Path,
    home: Path,
    prerequisites: Prerequisites,
) -> InstallPaths:
    """Atomically refresh the launcher and Chrome manifest, rolling back failure."""
    validate_extension_id(extension_id)
    repo_root = repo_root.resolve()
    if not (repo_root / "native_host" / "host.py").is_file():
        raise SetupError("native_host/host.py was not found in this checkout.")
    paths = install_paths(home)
    launcher = _launcher_text(repo_root, prerequisites)
    manifest = {
        "name": HOST_NAME,
        "description": "eduonline video downloader native host",
        "path": str(paths.launcher.resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    backups = {path: _snapshot(path) for path in (paths.launcher, paths.manifest)}
    try:
        _atomic_write(paths.launcher, launcher.encode("utf-8"), 0o700)
        _atomic_write(
            paths.manifest,
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            0o600,
        )
    except OSError as exc:
        for path, snapshot in backups.items():
            _restore(path, snapshot)
        raise SetupError("Native-host registration could not be written atomically.") from exc
    return paths


def diagnose(*, home: Path, timeout_seconds: float = 5.0) -> dict:
    """Validate installed files, origin, tools, and an actual native ping."""
    paths = install_paths(home)
    try:
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SetupError("Native-host manifest is not installed. Run the install command.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError("Native-host manifest is unreadable or invalid JSON.") from exc
    if set(manifest) != {"name", "description", "path", "type", "allowed_origins"}:
        raise SetupError("Native-host manifest has unexpected fields.")
    if manifest["name"] != HOST_NAME or manifest["type"] != "stdio":
        raise SetupError("Native-host manifest identity or type is invalid.")
    origins = manifest.get("allowed_origins")
    if not isinstance(origins, list) or len(origins) != 1 or not re.fullmatch(
        r"chrome-extension://[a-p]{32}/", str(origins[0])
    ):
        raise SetupError("Native-host manifest must authorize exactly one valid extension origin.")
    launcher = Path(str(manifest["path"]))
    if launcher != paths.launcher.resolve() or not launcher.is_file() or not os.access(str(launcher), os.X_OK):
        raise SetupError("The registered launcher path is missing or not executable.")
    reply = _ping_launcher(launcher, timeout_seconds)
    if reply.get("event") != "result" or reply.get("payload", {}).get("status") != "ok":
        raise SetupError("The native host did not complete the protocol handshake.")
    versions = {
        "python": _tool_version([sys.executable, "--version"]),
        "yt-dlp": _tool_version(["yt-dlp", "--version"]),
        "ffmpeg": _tool_version(["ffmpeg", "-version"]),
    }
    return {"manifest": str(paths.manifest), "launcher": str(launcher), "origin": origins[0], "versions": versions}


def remove(*, home: Path) -> list[Path]:
    """Remove only this project's generated manifest and launcher."""
    paths = install_paths(home)
    removed = []
    for path in (paths.manifest, paths.launcher):
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    for directory in (paths.launcher.parent, paths.launcher.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def _launcher_text(repo_root: Path, prerequisites: Prerequisites) -> str:
    environment = f"PYTHONPATH={shlex.quote(str(repo_root))}"
    command = " ".join(
        shlex.quote(str(part))
        for part in (prerequisites.python, "-m", "native_host.host")
    )
    path = os.pathsep.join(
        dict.fromkeys([str(prerequisites.yt_dlp.parent), str(prerequisites.ffmpeg.parent), "/usr/bin", "/bin"])
    )
    return f"#!/bin/sh\nexport PATH={shlex.quote(path)}\nexport {environment}\nexec {command}\n"


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _snapshot(path: Path):
    if not path.exists():
        return None
    return path.read_bytes(), path.stat().st_mode & 0o777


def _restore(path: Path, snapshot) -> None:
    if snapshot is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    content, mode = snapshot
    _atomic_write(path, content, mode)


def _ping_launcher(launcher: Path, timeout_seconds: float) -> dict:
    request = json.dumps(
        {"version": 2, "requestId": "diagnostic-ping", "action": "ping", "payload": {}},
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        result = subprocess.run(
            [str(launcher)],
            input=struct.pack("<I", len(request)) + request,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError("The native-host launcher could not complete a handshake.") from exc
    if result.returncode != 0 or len(result.stdout) < 4:
        raise SetupError("The native-host launcher exited before replying.")
    (length,) = struct.unpack("<I", result.stdout[:4])
    if length <= 0 or length > 1024 * 1024 or len(result.stdout) != length + 4:
        raise SetupError("The native-host handshake returned invalid framing.")
    try:
        return json.loads(result.stdout[4:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupError("The native-host handshake returned invalid JSON.") from exc


def _tool_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"Tool is not runnable: {command[0]}") from exc
    if result.returncode != 0:
        raise SetupError(f"Tool returned an error: {command[0]}")
    output = (result.stdout or result.stderr).splitlines()
    return output[0][:200] if output else "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install", help="register the native host")
    install_parser.add_argument("extension_id")
    subparsers.add_parser("diagnose", help="validate registration and handshake")
    subparsers.add_parser("remove", help="remove only generated host files")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    home = Path.home()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "install":
            extension_id = validate_extension_id(args.extension_id)
            prerequisites = preflight(home=home)
            paths = install(extension_id, repo_root=repo_root, home=home, prerequisites=prerequisites)
            print(f"Installed {HOST_NAME}\nManifest: {paths.manifest}\nLauncher: {paths.launcher}")
            print(f"Python: {prerequisites.python}\nyt-dlp: {prerequisites.yt_dlp}\nffmpeg: {prerequisites.ffmpeg}")
        elif args.command == "diagnose":
            print(json.dumps(diagnose(home=home), indent=2))
        else:
            removed = remove(home=home)
            print("Removed:\n" + ("\n".join(str(path) for path in removed) or "Nothing was installed."))
        return 0
    except SetupError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
