## Why

Learners who are authorized to view videos on eduonline.io currently have no simple browser-to-`yt-dlp` workflow for saving an individual lesson for offline viewing. A Chrome extension backed by a local native-messaging host can provide that workflow while keeping media processing and authenticated access on the user's machine.

## What Changes

- Add a Chrome Manifest V3 extension that recognizes supported `*.eduonline.io/learn/...` lesson pages and discovers every supported `v.accelsite.io` player iframe rendered by the active lesson.
- Add a popup that shows the lesson's discovered videos, lets the user select a video and one of its probed qualities, and reports download state and actionable errors.
- Add a local Python native-messaging host that resolves the allowlisted player iframe to a Kinescope `master.m3u8`, probes it with the required origin/referrer headers, downloads the chosen format with `yt-dlp`, and saves the result under `~/Downloads`.
- Add a macOS installation and diagnostics workflow for registering the native host and checking Python, `yt-dlp`, and `ffmpeg` prerequisites.
- Limit the first release to one selected download at a time from the active, already-authorized lesson. Course-wide downloads, DRM bypass, and Windows/Linux installers are out of scope.

## Capabilities

### New Capabilities

- `eduonline-video-discovery`: Resolve all supported AccelSite/Kinescope players rendered by an eduonline lesson and present their metadata and per-video HLS qualities in the extension popup.
- `native-video-download`: Exchange validated native-messaging requests with a local Python host and download a selected video format through `yt-dlp`.
- `native-host-setup`: Install, register, verify, and troubleshoot the native-messaging host on macOS.

### Modified Capabilities

None.

## Impact

- Introduces a Chrome MV3 extension (manifest, popup, content script, and service-worker/native-messaging integration).
- Introduces a Python 3 native host plus a Chrome native-host manifest and macOS installer.
- Adds runtime dependencies on Google Chrome, Python 3.8+, `yt-dlp`, and `ffmpeg`.
- Requests narrowly scoped access to eduonline.io lesson pages and the `nativeMessaging` capability; player resolution and media processing remain local and no credentials are stored by the extension.
- Adds an allowlisted native-host integration with `v.accelsite.io` player pages and `kinescope.io` HLS manifests. The supplied lesson was verified to expose separate non-DRM video and audio formats when the player origin/referrer headers are supplied; protected DRM streams remain explicitly unsupported.
- Keeps DOM order for distinct videos, deduplicates repeated player/media identities, and allows one broken candidate to be reported or skipped without hiding other valid videos on the lesson.
