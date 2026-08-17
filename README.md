# eduonline Video Downloader

A Chrome MV3 extension plus a local Python native-messaging host for downloading videos from eduonline lessons that you are authorized to view. It recognizes the observed provider chain:

`*.eduonline.io/learn/...` → `https://v.accelsite.io/v/<id>` → `https://kinescope.io/<id>/master.m3u8`

The extension discovers every supported embedded player and presents a video selector with per-video metadata and quality choices. The native host performs the constrained `yt-dlp` probe/download and writes the selected result under `~/Downloads`.

## Install on macOS

The extension requires:

- macOS with Google Chrome at `/Applications/Google Chrome.app`;
- Python 3.8 or newer;
- `yt-dlp` and `ffmpeg` available on `PATH`.

### 1. Download the project

Open Terminal and clone the repository to a permanent location. Do not move or delete this directory after registering the native host.

```sh
git clone https://github.com/akaUNik/eduonline-chrome-extension.git
cd eduonline-chrome-extension
```

### 2. Install and verify the native dependencies

Install the media tools with Homebrew:

```sh
brew install yt-dlp ffmpeg
```

Verify all three commands before continuing:

```sh
python3 --version
yt-dlp --version
ffmpeg -version
```

The host installer repeats these checks and exits without writing registration files if Python 3.8+, `yt-dlp`, `ffmpeg`, or Google Chrome is missing.

### 3. Load the Chrome extension

1. Open `chrome://extensions` in Google Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked**.
4. Select the cloned `eduonline-chrome-extension/extension` directory.
5. Copy the 32-character ID shown on the extension card. An unpacked extension can have a different ID on another Mac, so always use the ID displayed by that Chrome installation.

### 4. Register the native host

From the cloned repository root, replace `YOUR_EXTENSION_ID` with the ID copied from Chrome:

```sh
python3 scripts/setup_host.py install YOUR_EXTENSION_ID
python3 scripts/setup_host.py diagnose
```

Installation is successful only when `diagnose` prints the native-message handshake status and the detected Python, `yt-dlp`, and `ffmpeg` versions without an error.

### 5. Finish installation

Return to `chrome://extensions`, click **Reload** on the extension card, and reload any already-open eduonline lesson tab. Pin the extension from Chrome's Extensions menu if desired.

If the cloned directory is moved later, rerun the `install` and `diagnose` commands from its new location. Registration is idempotent and refreshes the absolute launcher path.

## Package and distribute the extension

The packed Chrome extension contains only the files under `extension/`. It does
not contain the Python native host, `yt-dlp`, or `ffmpeg`. Every recipient must
therefore keep a copy of this repository, install the native dependencies, and
register the native host for the installed extension ID as described above.

### Create the package

Before publishing a new release, increment `version` in
`extension/manifest.json` and run the test suite:

```sh
npm test
```

To create the first package in Chrome:

1. Open `chrome://extensions` and enable **Developer mode**.
2. Click **Pack extension**.
3. Set **Extension root directory** to this repository's `extension` directory.
4. Leave **Private key file** empty for the first package.
5. Chrome creates `extension.crx` and `extension.pem` next to the `extension`
   directory.

The same package can be created from Terminal on macOS:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --pack-extension="$PWD/extension"
```

Keep `extension.pem` secret, outside public release archives, and never commit
it. The private key determines the extension ID. Losing or replacing it changes
the ID, breaks updates, and requires every installation to register the native
host again for the new ID.

For every update, increment the manifest version and pack with the original key:

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --pack-extension="$PWD/extension" \
  --pack-extension-key="$PWD/extension.pem"
```

After installing the package, copy its ID from `chrome://extensions`, then run:

```sh
python3 scripts/setup_host.py install YOUR_PACKED_EXTENSION_ID
python3 scripts/setup_host.py diagnose
```

### Choose a distribution channel

- **Chrome Web Store** is the supported option for ordinary macOS and Windows
  users. Publish the extension package through the Store, and distribute the
  repository or a separate native-host installer alongside it.
- **Managed organization** deployments on macOS, Windows, or Linux can
  self-host the `.crx` and install it with Chrome enterprise policies. For
  automatic updates, host an update manifest, add its HTTPS `update_url` to
  `manifest.json`, and always sign updates with the same private key.
- **Linux only** supports manual installation of a packed extension that is not
  signed and distributed by the Chrome Web Store.
- **Local development or a small trusted test group** can continue using
  **Load unpacked** with the `extension` directory. Each installation may have
  a different ID and must register the native host with that displayed ID.

See Chrome's official documentation for
[extension distribution](https://developer.chrome.com/docs/extensions/how-to/distribute),
[Linux self-hosting and updates](https://developer.chrome.com/docs/extensions/how-to/distribute/host-on-linux),
and [enterprise `ExtensionSettings`](https://support.google.com/chrome/a/answer/9867568).

## Use

1. Sign in normally and open an eduonline lesson containing an AccelSite video.
2. Click the extension icon. The popup inspects only the active lesson's rendered iframe URLs.
3. If the lesson contains multiple videos, select one by title.
4. Select **Best**, an explicit resolution, or **Audio only (MP3)** when audio is available.
5. Click **Download**. The native host keeps running if the popup closes; reopening it restores the latest progress while that host session remains connected.
6. Find the collision-safe, title-based output in `~/Downloads`.

Use this only for media you are permitted to download. The extension does not bypass DRM or access controls.

## Provider limitations

This release intentionally supports only:

- an HTTP(S) eduonline lesson beneath `/learn/`;
- an HTTPS iframe at `v.accelsite.io/v/<opaque-id>` with only the observed boolean player query fields;
- an HTTPS Kinescope manifest at `kinescope.io/<opaque-id>/master.m3u8`;
- an optional poster at `cdn.app.axl.tech`.

Kinescope probe/download requests add only these fixed, non-secret headers:

```text
Origin: https://v.accelsite.io
Referer: https://v.accelsite.io/
```

Other providers, arbitrary URLs, signed manifest queries, cross-host redirects, DRM, and authorization failures are rejected. Browser cookies are never requested or read.

## Troubleshooting

Run diagnostics first:

```sh
python3 scripts/setup_host.py diagnose
```

Common cases:

- `NATIVE_HOST_UNAVAILABLE`: rerun `install` with the ID currently shown in `chrome://extensions`, run `diagnose`, then reload the extension.
- `YTDLP_NOT_FOUND` or `FFMPEG_NOT_FOUND`: install the missing Homebrew package and rerun setup.
- `AUTHORIZATION_REQUIRED`: confirm the lesson plays normally. The downloader does not extract cookies or offer a bypass.
- `DRM_UNSUPPORTED`: protected media is outside this tool's scope.
- `PROBE_EXPIRED`: close and reopen the popup to probe again.
- No player found: start playback or wait for the lesson to render its AccelSite iframe, then reopen the popup.

Host diagnostics go only to stderr and redact URL details and credential-like fields. Probe tokens, manifest URLs, format selectors, and headers are retained only in native-host memory; `chrome.storage.session` receives only sanitized popup state.

## Remove

```sh
python3 scripts/setup_host.py remove
```

Removal deletes only this host's generated launcher and `io.eduonline.ytdlp.json`. It does not delete downloads, the repository, Chrome data, or unrelated native hosts. Remove the unpacked extension separately from `chrome://extensions`.

## Develop and test

```sh
npm test
openspec validate add-eduonline-video-downloader --strict
```

The test suite uses sanitized player/manifest fixtures and fake downloader executables; it does not contact eduonline, AccelSite, or Kinescope.
