## Context

The repository currently has no implementation. The requested reference project demonstrates the basic Chrome MV3 → native messaging → Python → `yt-dlp` topology, but its extractor and macOS-specific host are built for YouTube. Inspection of the supplied authorized lesson established the actual media chain: a lesson may embed one or more `https://v.accelsite.io/v/<opaque-id>` frames, whose HTML initializes `AccelPlayer` with title, duration, poster, and a `https://kinescope.io/<opaque-id>/master.m3u8` URL.

A read-only probe with `yt-dlp 2026.07.04` verified that the Kinescope manifest returns separate non-DRM audio plus 360p, 480p, and 720p video formats. Kinescope rejects an unaffiliated probe with HTTP 403 but succeeds when requests include the fixed `Origin: https://v.accelsite.io` and `Referer: https://v.accelsite.io/` headers. No browser cookies are required.

Chrome content scripts cannot read inside cross-origin player frames, popup lifetime is short, and MV3 service workers are suspendable. Native messaging also treats stdout as a framed protocol channel, so all diagnostics must go to stderr or a private redacted log. The design therefore separates page discovery, durable extension orchestration, and native media work.

## Goals / Non-Goals

**Goals:**

- Keep Chrome permissions and accepted native-host inputs narrowly scoped.
- Base quality choices on a real `yt-dlp` probe rather than DOM labels or guessed format strings.
- Continue an active download when the popup closes and restore its latest state when reopened.
- Present every distinct supported video rendered by the active lesson and keep metadata, qualities, and probe tokens scoped to the selected video.
- Make installation and failure diagnosis reproducible on macOS.
- Keep the verified AccelSite/Kinescope adapter isolated so another provider can be added without weakening its URL rules.

**Non-Goals:**

- Reimplement HLS/DASH downloading or media merging in JavaScript or Python.
- Intercept or decrypt encrypted media extensions, license requests, or DRM keys.
- Obtain access beyond the user's existing eduonline enrollment/session.
- Support arbitrary embeds or Kinescope URLs that are not resolved from the active eduonline lesson's AccelSite iframe.
- Add a packaged Chrome Web Store release or signed macOS installer in the first version.

## Decisions

### 1. Use four components with a small versioned protocol

The runtime flow will be:

```text
popup ⇄ background service worker ⇄ Python native host ⇄ yt-dlp/ffmpeg
                    ⇅
              content script
```

- The content script validates the active lesson and returns its page title plus bounded HTTPS player `iframe` URLs; it never needs access inside a cross-origin frame.
- The background service worker owns the long-lived `chrome.runtime.connectNative()` port, request correlation, the single active-download state machine, in-memory per-video probe tokens, and a sanitized snapshot in `chrome.storage.session`.
- The popup renders discovery, a video selector only when multiple videos exist, selected-video metadata and qualities, progress, completion, and categorized errors. It never launches native commands directly.
- The Python host implements framing, schema validation, candidate probing, the in-memory probe cache, subprocess control, and progress parsing.

Protocol v2 messages contain `version`, `requestId`, `action`, and `payload`. Responses add `event` (`result`, `progress`, or `error`) and a stable error code. The probe result contains an ordered `videos` array rather than one media object. Input size, candidate count, video count, and string lengths are bounded. The incompatible version bump ensures protocol-v1 and protocol-v2 components fail explicitly instead of misinterpreting the response shape.

Alternative considered: call `sendNativeMessage()` separately for probe and download. Rejected because each call creates a short-lived host and cannot preserve probe tokens, stream progress, reject concurrent work reliably, or outlive the popup.

### 2. Resolve every distinct verified AccelSite player shape

The content script returns iframe candidates in rendered DOM order only when they are HTTPS on `v.accelsite.io` and their paths match `/v/<opaque-id>`. It bounds the list and deduplicates identical player URLs. The native host fetches each remaining player page with bounded response size and timeout, then extracts only these expected `AccelPlayer` fields:

- `videoId` as an opaque identifier;
- `stream.durationInSec` as a bounded integer;
- `url` as an HTTPS Kinescope `/<opaque-id>/master.m3u8` URL;
- `title` and `ui.poster` as optional display metadata.

The host parses these fields as text/data and never evaluates the inline JavaScript. It HTML/JavaScript-string decodes only the captured scalar values, applies length limits, validates every resulting URL independently, and deduplicates candidates that resolve to the same canonical media identity. Sanitized player HTML and manifest metadata from the verified lesson become test fixtures with all real identifiers replaced.

Alternative considered: observe direct `*.kinescopecdn.net` MP4 range requests with `chrome.webRequest`. Rejected because the player page already exposes a master manifest containing every quality, while network observation would add permission surface, signed URL handling, and a play-before-discovery requirement.

### 3. Probe the resolved HLS manifest in the native host

The host runs `yt-dlp --dump-single-json --no-playlist` for each distinct validated Kinescope manifest with fixed AccelSite origin/referrer headers and normalizes every successful result into title, poster, duration, canonical media identity, and quality choices. AccelPlayer metadata overrides the generic manifest title when present. The host groups redundant formats by height/container and returns opaque choice IDs for:

- best available video plus audio;
- distinct supported video heights, selecting the best compatible streams at or below that height;
- audio-only when an audio stream exists.

The host returns successful videos in candidate order and stores each canonical URL and its exact format selectors behind an independent random, short-lived probe token. A failed candidate does not hide successful videos; the host returns a categorized error only when every candidate fails. A download request contains one selected video's token and choice ID, not a URL or raw `-f` expression. Tokens are invalidated when the host exits and expire after a short idle period.

Alternative considered: parse `<video>` metadata and build `yt-dlp -f` expressions in the popup. Rejected because adaptive-stream manifests and separate audio/video formats are not represented reliably in the DOM.

### 4. Use a closed URL and request-header policy

The native host accepts player pages only from HTTPS `v.accelsite.io/v/<opaque-id>` and manifests only from HTTPS `kinescope.io/<opaque-id>/master.m3u8`. URLs with credentials, fragments, unexpected query parameters, ports, IP literals, or non-matching paths are rejected. Redirects are disabled or revalidated at every hop before the body is consumed.

Kinescope probe and download commands add exactly the fixed public player headers `Origin: https://v.accelsite.io` and `Referer: https://v.accelsite.io/`. The extension does not request Chrome's `cookies` or `webRequest` permissions, and the host never reads a browser profile. Logs retain provider host and stable error category only; query strings, headers, subprocess command lines, player HTML, and raw extractor output are not logged.

Alternative considered: pass browser cookies or captured CDN request headers to the host. Rejected because the verified HLS probe does not require them and they would create unnecessary credential-bearing paths.

### 5. Run downloads on one worker while the host read loop remains responsive

The Python process keeps reading native messages on its main loop and runs the sole active `yt-dlp` process on a worker. Writes to stdout pass through one framing lock. This allows `status` and a second `download` request to receive deterministic replies while work is active.

The host invokes an argument array with `shell=False`, a fixed `~/Downloads` root, `--no-playlist`, the fixed origin/referrer headers, `--newline`, machine-readable download and postprocessor progress templates, an `after_move` filepath marker, and host-owned format/output templates. A title plus media ID in the filename reduces collisions; `--no-overwrites` prevents silent replacement. `yt-dlp` selects the chosen HLS video plus audio selector, while audio conversion and separate-stream merge use the resolved `ffmpeg` executable. For a separate video-plus-audio selection, the host maps the first stream to 0–45 percent, the second to 45–90 percent, and verified Merger start/finish events to 90–100 percent while keeping reported progress non-decreasing; single-stream and audio-only downloads retain the full range.

The service worker persists only sanitized state: ordered video metadata without probe tokens or selectors, selected video identity, request ID, title, choice label, percentage, status, filename, and error code. Changing the selected video atomically changes the displayed title, optional poster/duration, qualities, and in-memory token used by a later download. The open native port is the primary keepalive while a download is active; the popup can reconnect to the worker at any time.

Alternative considered: block the host until `subprocess.run()` completes. Rejected because it provides no progress, busy response, or usable reopened-popup state.

### 6. Install a stable launcher and generated per-user manifest

The macOS installer validates dependencies and extension ID before writing anything. It generates a launcher at a stable project-managed path that uses absolute Python and project host paths, then atomically writes the native host manifest under:

`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/io.eduonline.ytdlp.json`

The manifest authorizes exactly `chrome-extension://<extension-id>/`. Re-running setup replaces only this host's generated files. A diagnostics mode validates JSON, permissions, executable resolution, protocol handshake, and tool versions. Removal deletes only the generated launcher/manifest and leaves source and downloads untouched.

Alternative considered: register the repository's Python file directly. Rejected because Chrome launches native hosts with a minimal environment and does not guarantee the user's interactive `PATH`.

### 7. Test boundaries independently and include one authorized manual acceptance test

Unit tests cover player HTML parsing, redirect/URL/header policy, message validation, framing, ordered multi-candidate probing, duplicate player/media handling, partial candidate failure, HLS metadata normalization, choice mapping, redaction, output-path handling, and progress/error parsing without network access. Extension tests cover supported-tab detection, multi-iframe candidate extraction fixtures, single/multiple-video popup states, video switching, and service-worker state transitions with mocked Chrome/native APIs. Installer tests use a temporary home-like directory.

One manual acceptance test in an authorized Chrome profile must prove the supplied lesson end to end: metadata appears, at least one offered video quality downloads, the file lands in `~/Downloads`, and reopening the popup during the transfer restores state. This test records no cookies or signed URLs.

## Risks / Trade-offs

- [eduonline changes its player provider or iframe shape] → Keep candidate discovery in a small adapter, use fixtures, and report unsupported media rather than broadening permissions automatically.
- [A lesson contains duplicate, broken, or many player frames] → Bound candidates, preserve DOM order, deduplicate player and canonical media identities, and isolate candidate failures.
- [AccelSite changes its inline configuration syntax] → Parse a small set of bounded scalar fields, fail closed, and update sanitized fixtures rather than evaluating player JavaScript.
- [Kinescope changes its header or manifest requirements] → Keep fixed headers in the provider adapter, categorize 403 separately, and never fall back to cookies automatically.
- [MV3 lifecycle interrupts orchestration] → Keep the native port open during work, store a sanitized session snapshot, and reconcile state with the host when the popup reconnects.
- [A native process survives an unexpected browser disconnect] → Treat stdin EOF as cancellation/cleanup, terminate the child process group with a bounded grace period, and never detach downloads.
- [Parsing human-oriented `yt-dlp` output changes across versions] → Use JSON metadata, progress templates, and after-move markers; pin only a documented minimum version and test supported output samples.
- [Native host URL fetching creates an SSRF surface] → Use exact provider host/path rules, reject credentials/IP literals/unexpected queries, revalidate redirects, bound responses, use probe tokens, and accept no raw URL in download requests.

## Migration Plan

1. Convert the verified AccelPlayer HTML and HLS metadata shapes into sanitized fixtures.
2. Implement and test the native protocol/provider adapter, extension components, and macOS setup without registering them globally during automated tests.
3. Load the unpacked extension, run setup with its generated extension ID, and execute diagnostics.
4. Run the end-to-end acceptance test on the supplied lesson and verify the downloaded file.
5. Document manual installation, usage, diagnostics, AccelSite/Kinescope limitations, and removal.

Rollback consists of disabling/removing the unpacked extension and running the host removal workflow. Source files and already downloaded media remain unchanged.
