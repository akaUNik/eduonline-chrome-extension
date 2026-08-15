# native-video-download Specification

## Purpose

Provide a constrained native-messaging bridge that uses local `yt-dlp` tooling to inspect every supported video on an eduonline lesson and download one user-selected video at a time.

## Requirements

### Requirement: Versioned native messaging contract
The extension and native host SHALL exchange length-prefixed JSON messages using protocol v2 with a documented protocol version, request identifier, action, validated payload, and structured success or error response.

#### Scenario: Supported request is received
- **WHEN** the host receives a well-formed message with a supported protocol version and action
- **THEN** it returns responses correlated with the request identifier

#### Scenario: Invalid request is received
- **WHEN** a message is malformed, oversized, uses an unsupported version or action, or omits required fields
- **THEN** the host rejects it with a structured error and does not invoke `yt-dlp`

#### Scenario: Protocol v1 component connects to a v2 component
- **WHEN** an old extension or host sends a protocol-v1 request after the multi-video response contract is installed
- **THEN** the receiving component rejects the version explicitly instead of interpreting the incompatible response shape

### Requirement: Media probing
The native host SHALL independently fetch each distinct allowlisted AccelSite player page, extract only the expected AccelPlayer metadata and Kinescope HLS URL without executing page JavaScript, inspect each distinct manifest without downloading media, and return an ordered `videos` array containing normalized metadata and only formats that can be selected safely by a later download request.

#### Scenario: AccelSite candidate is supported
- **WHEN** the player configuration contains an allowlisted Kinescope `master.m3u8` that `yt-dlp` can inspect with the fixed AccelSite origin and referrer
- **THEN** the host includes a video entry containing its own probe token, title, optional poster and duration, and normalized selectable formats

#### Scenario: Multiple candidates are supported
- **WHEN** two or more distinct candidate players can be inspected successfully
- **THEN** the host returns each distinct media entry in candidate order with an independent probe token and format choices

#### Scenario: Candidates resolve to duplicate media
- **WHEN** repeated player URLs or different player pages resolve to the same canonical media identity
- **THEN** the host probes and returns that media once

#### Scenario: Candidate resolution fails
- **WHEN** one player page or HLS manifest is unsupported, inaccessible, protected, malformed, or invalid while another candidate succeeds
- **THEN** the host omits the failed candidate and returns the successful media entries

#### Scenario: Every candidate resolution fails
- **WHEN** no candidate can be resolved and probed successfully
- **THEN** the host returns a categorized error suitable for display in the popup

#### Scenario: Player configuration contains an untrusted URL
- **WHEN** the parsed stream or poster URL does not match its approved HTTPS host and path rules
- **THEN** the host rejects the configuration and does not invoke `yt-dlp`

### Requirement: Constrained download invocation
The native host SHALL map a media identifier and selected normalized format from a successful probe to internally constructed `yt-dlp` arguments, and SHALL NOT accept raw command-line arguments or shell syntax from the extension.

#### Scenario: Valid selection is downloaded
- **WHEN** the host receives a download request referencing a current probe result and one of its offered formats
- **THEN** it invokes `yt-dlp` without a shell using only host-constructed arguments

#### Scenario: Tampered selection is rejected
- **WHEN** a download request changes the media URL or names a format that was not returned by the referenced probe
- **THEN** the host rejects the request before starting a subprocess

### Requirement: Download output
The native host SHALL save completed files beneath the current user's `~/Downloads` directory, use a filesystem-safe title-based filename, avoid silently overwriting a different existing file, and use `ffmpeg` when merging or converting streams requires it.

#### Scenario: Video download completes
- **WHEN** the selected video and audio streams finish successfully
- **THEN** the merged file is present beneath `~/Downloads` and the completion response includes its filename

#### Scenario: Output filename already exists
- **WHEN** the target filename conflicts with an existing different download
- **THEN** the host chooses a collision-safe filename or reports that the identical item was already downloaded

#### Scenario: Audio-only download completes
- **WHEN** the user selects audio-only and the required tooling is available
- **THEN** the host saves the converted audio file beneath `~/Downloads`

### Requirement: Download status and failure reporting
The extension SHALL show that a download was accepted, expose progress when `yt-dlp` supplies it, and show a terminal completion or actionable failure without requiring the popup to remain continuously open.

#### Scenario: Popup is reopened during a download
- **WHEN** the popup is closed and reopened while the native host is processing the active download
- **THEN** the extension restores the latest known state for that download

#### Scenario: Separate video and audio streams report aggregate progress
- **WHEN** the selected video format requires separate video and audio downloads
- **THEN** video-stream progress occupies 0–45 percent and audio-stream progress occupies 45–90 percent of one non-decreasing overall indicator
- **AND** the verified start and finish of the merge occupy the remaining 90–100 percent
- **AND** an audio-only download continues to use the full 0–100 percent range

#### Scenario: Download fails
- **WHEN** `yt-dlp`, `ffmpeg`, storage, authorization, or network processing fails
- **THEN** the user sees a categorized error without secrets, cookies, or raw diagnostic dumps

### Requirement: Single active download
The first release SHALL run at most one download at a time and SHALL reject a second start request with a clear busy response.

#### Scenario: A second download is requested
- **WHEN** a download is already active and another valid download request arrives
- **THEN** the host leaves the first download running and reports that it is busy

### Requirement: Sensitive data handling
The extension and host SHALL NOT read browser cookies and SHALL NOT persist authorization headers, signed query strings, or other credentials in application logs or extension storage. Only fixed AccelSite origin/referrer headers SHALL be added to Kinescope probe and download requests.

#### Scenario: Diagnostics are recorded
- **WHEN** the host records an invocation result or error
- **THEN** the diagnostic output redacts sensitive URL components and authentication material
