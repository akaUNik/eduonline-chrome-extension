# Native messaging protocol v1 (historical)

Protocol v1 supported a single-video probe response and is retained only as a
compatibility reference. Current extension and host code use protocol v2 and
explicitly reject v1 messages.

Every message is UTF-8 JSON prefixed by a four-byte little-endian unsigned
length. Messages are limited to 1 MiB. Requests contain `version`,
`requestId`, `action`, and `payload`; host messages contain `version`,
`requestId`, `event`, and either `payload` or a structured `error`.

Supported actions are `ping`, `probe`, `download`, and `status`. A probe accepts
the active eduonline lesson URL and bounded AccelSite iframe candidates. A
download accepts only an unexpired probe token and a choice ID previously
returned for that token. Raw manifest URLs and yt-dlp arguments are never
accepted from a download request.

Stable events and error codes are defined in [`protocol/v1.json`](../protocol/v1.json),
with complete message examples in [`protocol/examples.json`](../protocol/examples.json).
Unknown versions, fields with invalid types or lengths, and unsupported actions
are rejected before any network or subprocess work.
