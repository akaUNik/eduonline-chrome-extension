# Native messaging protocol v2

Every message is UTF-8 JSON prefixed by a four-byte little-endian unsigned
length. Messages are limited to 1 MiB. Requests contain `version`,
`requestId`, `action`, and `payload`; host messages contain `version`,
`requestId`, `event`, and either `payload` or a structured `error`.

Supported actions are `ping`, `probe`, `download`, and `status`. A probe accepts
the active eduonline lesson URL and bounded AccelSite iframe candidates. Its
result contains an ordered `videos` array. Each entry has a stable media ID,
title, optional poster/duration, selectable formats, and an independent opaque
probe token. Duplicate player and canonical media identities occur only once;
candidate failures are isolated while at least one video succeeds.

A download accepts only an unexpired per-video probe token and a choice ID
previously returned for that token. Raw manifest URLs and yt-dlp arguments are
never accepted. Protocol-v1 requests are explicitly rejected because their
single-video probe response is incompatible with v2.

Stable events and error codes are defined in
[`protocol/v2.json`](../protocol/v2.json), with complete message examples in
[`protocol/examples.json`](../protocol/examples.json).
