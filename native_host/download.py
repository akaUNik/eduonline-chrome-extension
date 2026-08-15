"""Single-worker yt-dlp downloads for previously probed media."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional, TextIO

from native_host.errors import ErrorCode, HostError
from native_host.probe import FormatChoice, ProbeCache, ProbeRecord
from native_host.protocol import redact_text
from native_host.provider import PLAYER_ORIGIN, PLAYER_REFERER


PROGRESS_PREFIX = "EDUONLINE_PROGRESS:"
MERGE_PREFIX = "EDUONLINE_MERGE:"
FILE_PREFIX = "EDUONLINE_FILE:"
PROGRESS_PATTERN = re.compile(
    r"^EDUONLINE_PROGRESS:(?P<stream>[A-Za-z0-9_.+-]{1,128}):"
    r"\s*(?P<percent>\d+(?:\.\d+)?)%?$"
)
MERGE_PATTERN = re.compile(r"^EDUONLINE_MERGE:Merger:(?P<status>started|finished)$")
MAX_DIAGNOSTIC_LINES = 20
DOWNLOAD_TIMEOUT_SECONDS = 24 * 60 * 60
TERMINATION_GRACE_SECONDS = 3.0
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f/\\:]")


class DownloadManager:
    """Own at most one foreground process-group-backed download."""

    def __init__(
        self,
        cache: ProbeCache,
        emit: Callable[[str, str, dict[str, Any]], None],
        *,
        yt_dlp: Optional[str] = None,
        ffmpeg: Optional[str] = None,
        downloads_dir: Optional[Path] = None,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self._cache = cache
        self._emit = emit
        self._yt_dlp = yt_dlp or shutil.which("yt-dlp") or ""
        self._ffmpeg = ffmpeg or shutil.which("ffmpeg") or ""
        self._downloads_dir = (downloads_dir or Path.home() / "Downloads").resolve()
        self._popen = popen
        self._lock = threading.Lock()
        self._active_request_id: Optional[str] = None
        self._process: Optional[subprocess.Popen[str]] = None
        self._worker: Optional[threading.Thread] = None
        self._state: dict[str, Any] = {"state": "idle"}
        self._shutdown = False

    def start(self, request_id: str, token: str, choice_id: str) -> dict[str, Any]:
        """Validate and start a download worker, returning immediately."""
        record, choice = self._cache.resolve(token, choice_id)
        self._check_tools(choice)
        self._prepare_downloads_dir()
        with self._lock:
            if self._shutdown:
                raise HostError(ErrorCode.DOWNLOAD_FAILED, "The native host is shutting down.")
            if self._active_request_id is not None:
                raise HostError(ErrorCode.BUSY, "Another download is already in progress.")
            self._active_request_id = request_id
            self._state = {
                "state": "accepted",
                "requestId": request_id,
                "title": record.title,
                "choiceId": choice.choice_id,
                "percent": 0.0,
            }
        worker = threading.Thread(
            target=self._run,
            args=(request_id, record, choice),
            name="eduonline-download",
            daemon=False,
        )
        with self._lock:
            self._worker = worker
        self._send_event(request_id, "accepted", self.status())
        worker.start()
        return self.status()

    def status(self) -> dict[str, Any]:
        """Return a sanitized snapshot suitable for extension storage."""
        with self._lock:
            return dict(self._state)

    def shutdown(self) -> None:
        """Stop the active child process group when Chrome disconnects."""
        with self._lock:
            self._shutdown = True
            process = self._process
            worker = self._worker
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=TERMINATION_GRACE_SECONDS + 2)

    def _check_tools(self, choice: FormatChoice) -> None:
        if not self._yt_dlp or not os.access(self._yt_dlp, os.X_OK):
            raise HostError(ErrorCode.YTDLP_NOT_FOUND, "yt-dlp is not installed or executable.")
        if not self._ffmpeg or not os.access(self._ffmpeg, os.X_OK):
            action = "convert audio" if choice.audio_only else "merge video and audio"
            raise HostError(ErrorCode.FFMPEG_NOT_FOUND, f"ffmpeg is required to {action}.")

    def _prepare_downloads_dir(self) -> None:
        try:
            self._downloads_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HostError(ErrorCode.STORAGE_ERROR, "The Downloads folder is not writable.") from exc
        if not self._downloads_dir.is_dir() or not os.access(self._downloads_dir, os.W_OK):
            raise HostError(ErrorCode.STORAGE_ERROR, "The Downloads folder is not writable.")

    def _run(self, request_id: str, record: ProbeRecord, choice: FormatChoice) -> None:
        diagnostics: deque[str] = deque(maxlen=MAX_DIAGNOSTIC_LINES)
        try:
            command = self._build_command(record, choice)
            process = self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
            with self._lock:
                self._process = process
                self._state["state"] = "downloading"
            self._send_event(request_id, "progress", self.status())
            timed_out = threading.Event()

            def stop_after_timeout() -> None:
                timed_out.set()
                _terminate_process_group(process)

            timer = threading.Timer(DOWNLOAD_TIMEOUT_SECONDS, stop_after_timeout)
            timer.daemon = True
            timer.start()
            try:
                filename = self._consume_output(process.stdout, request_id, diagnostics, choice)
                return_code = process.wait()
            finally:
                timer.cancel()
                if process.stdout is not None:
                    process.stdout.close()
            if timed_out.is_set():
                raise HostError(ErrorCode.DOWNLOAD_TIMEOUT, "The download timed out.")
            if return_code != 0:
                raise _categorize_download_failure("\n".join(diagnostics))
            if not filename:
                filename = _safe_title(record.title, record.media_id)
            final = {"state": "complete", "requestId": request_id, "filename": filename, "percent": 100.0}
            with self._lock:
                self._state = final
                self._active_request_id = None
            self._send_event(request_id, "complete", final)
        except subprocess.TimeoutExpired:
            with self._lock:
                process = self._process
            if process is not None:
                _terminate_process_group(process)
            self._finish_error(request_id, HostError(ErrorCode.DOWNLOAD_TIMEOUT, "The download timed out."))
        except HostError as exc:
            self._finish_error(request_id, exc)
        except (OSError, ValueError) as exc:
            self._finish_error(
                request_id,
                HostError(ErrorCode.DOWNLOAD_FAILED, "The local downloader could not be started.", redact_text(str(exc))),
            )
        finally:
            with self._lock:
                self._process = None
                self._active_request_id = None
                self._worker = None

    def _consume_output(
        self,
        stream: Optional[TextIO],
        request_id: str,
        diagnostics: deque[str],
        choice: FormatChoice,
    ) -> Optional[str]:
        filename: Optional[str] = None
        if stream is None:
            return None
        combined_streams = not choice.audio_only and "+" in choice.selector
        stream_slots: dict[str, int] = {}
        last_percent = 0.0
        for raw_line in stream:
            line = raw_line.strip()
            progress = PROGRESS_PATTERN.fullmatch(line)
            if progress:
                raw_percent = min(100.0, max(0.0, float(progress.group("percent"))))
                if combined_streams:
                    stream_id = progress.group("stream")
                    if stream_id not in stream_slots:
                        stream_slots[stream_id] = min(len(stream_slots), 1)
                    percent = stream_slots[stream_id] * 45.0 + raw_percent * 0.45
                else:
                    percent = raw_percent
                percent = max(last_percent, percent)
                last_percent = percent
                with self._lock:
                    self._state.update(state="downloading", percent=percent)
                self._send_event(request_id, "progress", self.status())
            elif combined_streams and (merge := MERGE_PATTERN.fullmatch(line)):
                percent = 90.0 if merge.group("status") == "started" else 100.0
                percent = max(last_percent, percent)
                last_percent = percent
                with self._lock:
                    self._state.update(state="merging", percent=percent)
                self._send_event(request_id, "progress", self.status())
            elif line.startswith(FILE_PREFIX):
                reported = Path(line[len(FILE_PREFIX) :].strip()).name
                if reported:
                    filename = reported[:255]
            elif line:
                diagnostics.append(redact_text(line)[:500])
        return filename

    def _build_command(self, record: ProbeRecord, choice: FormatChoice) -> list[str]:
        basename = _safe_title(record.title, record.media_id)
        output = str(self._downloads_dir / f"{basename}.%(ext)s")
        command = [
            self._yt_dlp,
            "--ignore-config",
            "--no-playlist",
            "--newline",
            "--progress",
            "--progress-delta",
            "0.5",
            "--no-overwrites",
            "--add-header",
            f"Origin:{PLAYER_ORIGIN}",
            "--referer",
            PLAYER_REFERER,
            "--ffmpeg-location",
            self._ffmpeg,
            "--progress-template",
            f"download:{PROGRESS_PREFIX}%(info.format_id)s:%(progress._percent_str)s",
            "--progress-template",
            f"postprocess:{MERGE_PREFIX}%(progress.postprocessor)s:%(progress.status)s",
            "--print",
            f"after_move:{FILE_PREFIX}%(filepath)s",
            "--output",
            output,
            "--format",
            choice.selector,
        ]
        if choice.audio_only:
            command.extend(["--extract-audio", "--audio-format", "mp3"])
        else:
            command.extend(["--merge-output-format", "mp4"])
        command.append(record.manifest_url)
        return command

    def _finish_error(self, request_id: str, error: HostError) -> None:
        state = {
            "state": "error",
            "requestId": request_id,
            "error": {"code": error.code.value, "message": error.public_message},
        }
        with self._lock:
            self._state = state
            self._active_request_id = None
        self._send_event(request_id, "error", state["error"])

    def _send_event(self, request_id: str, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            shutdown = self._shutdown
        if not shutdown:
            self._emit(request_id, event, payload)


def _safe_title(title: str, media_id: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:140].strip(" .") or "Lesson video"
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", media_id)[:64] or "media"
    return f"{cleaned} [{safe_id}]"


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        process_group = os.getpgid(process.pid)
        os.killpg(process_group, signal.SIGTERM)
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except (ProcessLookupError, ChildProcessError):
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _categorize_download_failure(details: str) -> HostError:
    lowered = details.lower()
    safe = redact_text(details)[:1000]
    if any(term in lowered for term in ("http error 401", "http error 403", "forbidden")):
        return HostError(ErrorCode.AUTHORIZATION_REQUIRED, "The video provider denied the download.", safe)
    if any(term in lowered for term in ("no space left", "permission denied", "read-only file system")):
        return HostError(ErrorCode.STORAGE_ERROR, "The file could not be written to Downloads.", safe)
    if "ffmpeg" in lowered:
        return HostError(ErrorCode.FFMPEG_NOT_FOUND, "ffmpeg could not merge or convert the media.", safe)
    if any(term in lowered for term in ("timed out", "network", "unable to download", "connection")):
        return HostError(ErrorCode.NETWORK_ERROR, "The media download failed because of a network error.", safe)
    return HostError(ErrorCode.DOWNLOAD_FAILED, "The local downloader could not complete the request.", safe)
