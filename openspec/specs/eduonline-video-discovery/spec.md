# eduonline-video-discovery Specification

## Purpose

Detect every downloadable video associated with the active eduonline.io lesson and expose trustworthy per-video metadata and quality choices to the user.

## Requirements

### Requirement: Supported lesson recognition
The extension SHALL activate discovery only for an HTTP(S) page on an `eduonline.io` host whose path represents a lesson under `/learn/`.

#### Scenario: User opens a supported lesson
- **WHEN** the active tab is an `https://*.eduonline.io/learn/...` lesson page
- **THEN** the popup begins video discovery for that tab

#### Scenario: User opens an unrelated page
- **WHEN** the active tab is not a supported eduonline.io lesson page
- **THEN** the popup explains that the page is unsupported and does not offer a download action

### Requirement: Active lesson video discovery
The extension SHALL collect the active lesson page URL and the rendered lesson's bounded HTTPS player frame URLs, and SHALL ask the native host to resolve every distinct allowlisted `v.accelsite.io/v/...` frame to its AccelPlayer configuration and Kinescope HLS manifest.

#### Scenario: AccelSite player is discoverable
- **WHEN** the rendered lesson exposes an HTTPS `v.accelsite.io/v/...` frame containing a valid AccelPlayer configuration
- **THEN** the extension identifies its allowlisted Kinescope HLS manifest as a downloadable video for the active lesson

#### Scenario: Lesson contains multiple supported videos
- **WHEN** the rendered lesson exposes two or more distinct supported AccelSite players
- **THEN** the extension returns every successfully resolved video in rendered iframe order

#### Scenario: Lesson repeats the same media
- **WHEN** duplicate iframe URLs or different iframe candidates resolve to the same media identity
- **THEN** the extension presents that media only once

#### Scenario: One candidate fails while another succeeds
- **WHEN** at least one supported candidate resolves successfully and another candidate is inaccessible, malformed, protected, or unsupported
- **THEN** the extension presents the successfully resolved videos without treating the whole lesson as failed

#### Scenario: No supported video is discoverable
- **WHEN** the lesson has no candidate that resolves to a valid supported Kinescope HLS manifest
- **THEN** the popup reports that no supported video was found and keeps the download action disabled

#### Scenario: Untrusted player target is present
- **WHEN** an iframe or parsed media URL uses an unapproved scheme, host, or path shape
- **THEN** the target is rejected before any media probe or download is started

#### Scenario: Lesson changes without a full page load
- **WHEN** client-side navigation changes the active eduonline lesson
- **THEN** reopening or refreshing the popup discovers metadata for the new lesson rather than reusing stale metadata

### Requirement: Video metadata presentation
The popup SHALL show each discovered video's title and SHALL show its poster and duration when those values are present in the validated AccelPlayer configuration, while clearly representing the loading and failure states.

#### Scenario: Multiple videos are available
- **WHEN** discovery returns more than one distinct video
- **THEN** the popup provides a video selector in rendered iframe order and displays metadata for the selected video

#### Scenario: One video is available
- **WHEN** discovery returns exactly one video
- **THEN** the popup displays it directly without requiring a redundant video selection step

#### Scenario: Complete metadata is available
- **WHEN** the selected video has title, thumbnail, and duration
- **THEN** the popup displays all three values for that video

#### Scenario: Optional metadata is absent
- **WHEN** the selected video has a title but no thumbnail or duration
- **THEN** the popup still permits format selection without fabricating the missing values

### Requirement: Download quality choices
The popup SHALL derive quality choices independently for each video from formats confirmed usable by probing that video's Kinescope HLS manifest with the required AccelSite origin and referrer, present the selected video's choices in descending video resolution with a best-quality default, and include audio-only only when that video has an audio format.

#### Scenario: User changes the selected video
- **WHEN** the user selects another discovered video
- **THEN** the popup updates title, optional metadata, quality choices, and download token scope to that video

#### Scenario: Multiple video qualities are available
- **WHEN** probing returns multiple usable video resolutions
- **THEN** the popup lists distinct quality choices in descending resolution and selects best quality by default

#### Scenario: Audio-only is available
- **WHEN** probing returns at least one usable audio stream
- **THEN** the popup includes an audio-only choice labelled with its intended output type

#### Scenario: No usable formats are available
- **WHEN** metadata is returned without a format the host can download
- **THEN** the popup disables download and reports that the video has no supported formats

### Requirement: Authorized and unprotected media only
The extension SHALL operate only on a player embedded in the active lesson, SHALL use only fixed non-secret AccelSite origin/referrer headers required by that player, and SHALL report DRM-protected or authorization-denied media as unsupported without attempting cookie extraction or access-control bypass.

#### Scenario: Protected media is encountered
- **WHEN** probing indicates DRM protection or an authorization failure with the fixed player headers
- **THEN** the popup reports the limitation and does not offer a bypass workflow
