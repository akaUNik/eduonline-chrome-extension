# Sanitized provider fixtures

These fixtures preserve only the verified structural shapes needed by tests.
All lesson titles, player IDs, stream IDs, asset IDs, and URLs are synthetic.

Provider policy:

- Player URL: HTTPS only, host exactly `v.accelsite.io`, default port only,
  path `/v/<opaque-id>`, and only the optional boolean query keys
  `showTitle`, `showControls`, and `muted`.
- Manifest URL: HTTPS only, host exactly `kinescope.io`, default port only,
  path `/<opaque-id>/master.m3u8`, with no query or fragment.
- Poster URL: HTTPS only on `cdn.app.axl.tech`; it is display metadata and is
  never passed to a subprocess.
- Kinescope requests use the fixed public headers
  `Origin: https://v.accelsite.io` and
  `Referer: https://v.accelsite.io/`.
- Browser cookies, captured authorization headers, and CDN request URLs are
  neither required nor stored.

`multi_video_iframes.json` models two distinct supported players in DOM order,
one repeated iframe, and one untrusted iframe. All identifiers are synthetic.
