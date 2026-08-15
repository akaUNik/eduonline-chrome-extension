## 1. Verify and Capture the eduonline Media Path

- [x] 1.1 Inspect the supplied lesson in an authorized Chrome session and record the sanitized `eduonline.io` → `v.accelsite.io/v/<id>` → `kinescope.io/<id>/master.m3u8` path, metadata fields, and non-DRM status without storing tokens or credentials.
- [x] 1.2 Run a read-only `yt-dlp --dump-single-json --no-playlist` probe of the Kinescope HLS manifest, verify 360p/480p/720p plus audio, and document that fixed AccelSite Origin/Referer headers work without browser cookies.
- [x] 1.3 Add sanitized AccelPlayer HTML and `yt-dlp` HLS metadata fixtures with real identifiers, titles, and URLs replaced, then document the exact provider host/path/header policy beside the fixtures.

## 2. Scaffold the Extension and Test Harnesses

- [x] 2.1 Create the Chrome MV3 extension structure with popup, content script, background service worker, icons, and only the eduonline host, native-messaging, and storage permissions required by the specs; do not request cookies or webRequest.
- [x] 2.2 Create standard-library Python tests for the native host and JavaScript tests with mocked Chrome APIs for extension modules, with one command that runs both suites.
- [x] 2.3 Define shared protocol-v1 examples and stable error codes for probe, download, progress, status, completion, busy, validation, setup, authorization, DRM, and tooling failures.

## 3. Implement the Native Protocol and Probe

- [x] 3.1 Implement bounded little-endian native-message framing, protocol/message schema validation, request correlation, serialized stdout writes, and redacted stderr diagnostics.
- [x] 3.2 Implement exact HTTPS validation for `v.accelsite.io/v/<opaque-id>` player URLs and `kinescope.io/<opaque-id>/master.m3u8` manifests, rejecting credentials, ports, fragments, unexpected queries, IP literals, unsafe redirects, excessive input, and all other hosts/paths.
- [x] 3.3 Implement bounded AccelSite player fetching and scalar AccelPlayer configuration parsing without JavaScript evaluation, followed by `yt-dlp` HLS probing with fixed AccelSite Origin/Referer headers and categorized failures.
- [x] 3.4 Normalize AccelPlayer plus HLS metadata into title, optional poster/duration, deduplicated descending video qualities, best-video, and conditional audio-only choices.
- [x] 3.5 Store canonical URLs and host-owned format selectors behind expiring random probe tokens, and reject unknown, expired, or tampered token/choice pairs.
- [x] 3.6 Add native-host unit tests for framing, exact URL/redirect/header policy, AccelPlayer parsing, redaction, HLS probing, metadata normalization, format choices, token expiry, 403 handling, malformed configuration, and representative extractor failures.

## 4. Implement Native Downloads

- [x] 4.1 Implement the single background download worker and responsive host read loop, including accepted, status, progress, completion, busy, and error events.
- [x] 4.2 Build fixed `shell=False` `yt-dlp` invocations for HLS video-plus-audio merge and audio-only conversion with `--no-playlist`, fixed AccelSite Origin/Referer headers, resolved `ffmpeg`, safe title/media-ID output templates, and no-overwrite behavior beneath `~/Downloads`.
- [x] 4.3 Parse machine-readable progress and final after-move filepath output, expose only a sanitized filename, and categorize network, authorization, storage, timeout, `yt-dlp`, and `ffmpeg` failures.
- [x] 4.4 Terminate the child process group cleanly on native stdin EOF or host shutdown and verify that subprocesses are not detached.
- [x] 4.5 Add unit/integration tests with fake `yt-dlp`/`ffmpeg` executables for success, progress, collision, audio, busy, failure, and disconnect cleanup paths.

## 5. Implement eduonline Discovery and Orchestration

- [x] 5.1 Implement supported-tab and `/learn/` URL recognition plus bounded extraction of HTTPS `v.accelsite.io/v/<opaque-id>` iframe candidates, without reading cross-origin frame contents.
- [x] 5.2 Implement background-service-worker ownership of `connectNative()`, correlated requests, reconnect/error handling, the single-download state machine, and sanitized `chrome.storage.session` snapshots.
- [x] 5.3 Implement popup loading, unsupported-page, metadata, missing-metadata, quality-selection, downloading, restored-progress, completion, and categorized-error states with download disabled whenever requirements are unmet.
- [x] 5.4 Ensure client-side lesson navigation cannot reuse stale probe results by keying discovery state to the current tab ID and canonical lesson URL.
- [x] 5.5 Add extension tests for URL recognition, AccelSite iframe candidate fixtures, popup format sorting/audio visibility, native-host setup errors, state restoration, and stale-state invalidation.

## 6. Implement macOS Setup and Diagnostics

- [x] 6.1 Implement preflight checks for macOS, Chrome, Python 3.8+, `yt-dlp`, `ffmpeg`, writable target directories, and a valid Chrome extension ID before any registration write.
- [x] 6.2 Implement idempotent generation of an absolute executable launcher and atomic installation of `io.eduonline.ytdlp.json` with exactly one allowed extension origin.
- [x] 6.3 Implement diagnostics for manifest JSON, origin, paths, permissions, protocol handshake, and tool versions, plus scoped removal of only generated host files.
- [x] 6.4 Test setup, rerun-after-move, invalid-input, partial-failure, diagnostics, and removal against temporary home/application-support directories.

## 7. Validate and Document the User Workflow

- [x] 7.1 Run all automated tests and OpenSpec strict validation, and resolve every failure without weakening the security or behavior contracts.
- [x] 7.2 Load the unpacked extension, register its native host, and verify diagnostics in Google Chrome on macOS.
- [x] 7.3 Complete the authorized end-to-end test on the supplied lesson: metadata and qualities appear, a selected video downloads to `~/Downloads`, and reopening the popup restores in-progress state; retain only sanitized evidence.
- [x] 7.4 Write README instructions for prerequisites, unpacked installation, host registration, usage, AccelSite/Kinescope limitations and required fixed headers, troubleshooting, privacy/security behavior, removal, and test commands.

## 8. Support Multiple Videos on One Lesson

- [x] 8.1 Add a sanitized discovery fixture and regression coverage for a lesson containing two distinct supported AccelSite iframe candidates.
- [x] 8.2 Upgrade the native contract to protocol v2 and return an ordered `videos` array whose entries have independent probe tokens, metadata, and format choices.
- [x] 8.3 Probe every bounded candidate with duplicate player-URL and canonical-media-ID suppression, preserve rendered candidate order, and return partial success when at least one distinct video is valid.
- [x] 8.4 Update background orchestration to retain per-video probe tokens only in memory while persisting a sanitized ordered video list and selected-video identity in `chrome.storage.session`.
- [x] 8.5 Add a popup video selector only when multiple videos exist and atomically switch title, optional poster/duration, qualities, and download token scope with the selected video.
- [x] 8.6 Add Python and JavaScript regression tests for protocol mismatch, two-video ordering, duplicate candidates/media, partial failure, single-video compatibility, token isolation, state restoration, and video switching; run all tests and OpenSpec strict validation.
- [x] 8.7 Reload the unpacked extension and complete an authorized Chrome acceptance test showing both videos on the supplied lesson and successfully selecting and downloading the second video.
- [x] 8.8 Report separate video, audio, and merge work as one non-decreasing 0–100 percent indicator split into 45/45/10 ranges, while retaining full-range audio-only progress, with regression tests.
