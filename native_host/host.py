"""Long-lived Chrome native-messaging entry point."""

from __future__ import annotations

import sys
from typing import Any, BinaryIO, Optional

from native_host.download import DownloadManager
from native_host.errors import ErrorCode, HostError
from native_host.probe import ProbeService
from native_host.protocol import (
    MessageWriter,
    PROTOCOL_VERSION,
    error_message,
    log_event,
    read_message,
    response_message,
)


class NativeHost:
    """Dispatch protocol requests while the download runs on its worker."""

    def __init__(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        probe_service: Optional[ProbeService] = None,
        download_manager: Optional[DownloadManager] = None,
    ) -> None:
        self.input_stream = input_stream
        self.writer = MessageWriter(output_stream)
        self.probe_service = probe_service or ProbeService()
        self.download_manager = download_manager or DownloadManager(
            self.probe_service.cache,
            self._emit_download_event,
        )

    def run(self) -> int:
        """Read until Chrome closes stdin, then clean up child processes."""
        try:
            while True:
                try:
                    request = read_message(self.input_stream)
                except HostError as exc:
                    log_event("invalid_message", code=exc.code.value)
                    self.writer.send(error_message("host", exc))
                    return 2
                if request is None:
                    return 0
                self._dispatch(request)
        finally:
            self.download_manager.shutdown()

    def _dispatch(self, request: dict[str, Any]) -> None:
        request_id = request["requestId"]
        action = request["action"]
        payload = request["payload"]
        try:
            if action == "ping":
                result = {"protocolVersion": PROTOCOL_VERSION, "status": "ok"}
                self.writer.send(response_message(request_id, "result", result))
            elif action == "probe":
                result = self.probe_service.probe(payload["lessonUrl"], payload["candidates"])
                self.writer.send(response_message(request_id, "result", result))
            elif action == "download":
                self.download_manager.start(
                    request_id,
                    payload["probeToken"],
                    payload["choiceId"],
                )
            elif action == "status":
                self.writer.send(response_message(request_id, "status", self.download_manager.status()))
            else:
                raise HostError(ErrorCode.UNSUPPORTED_ACTION, "Native request action is unsupported.")
        except HostError as exc:
            log_event("request_error", action=action, code=exc.code.value)
            self.writer.send(error_message(request_id, exc))

    def _emit_download_event(self, request_id: str, event: str, payload: dict[str, Any]) -> None:
        if event == "error":
            error = HostError(ErrorCode(payload["code"]), payload["message"])
            self.writer.send(error_message(request_id, error))
        else:
            self.writer.send(response_message(request_id, event, payload))


def main() -> int:
    """Run against the binary streams reserved by Chrome native messaging."""
    return NativeHost(sys.stdin.buffer, sys.stdout.buffer).run()


if __name__ == "__main__":
    raise SystemExit(main())
