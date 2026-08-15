## Purpose

Give macOS users a repeatable way to install, register, verify, troubleshoot, and remove the local native-messaging host and its prerequisites.

## ADDED Requirements

### Requirement: Prerequisite validation
The setup workflow SHALL verify a supported Python 3 interpreter, `yt-dlp`, `ffmpeg`, Google Chrome, and required filesystem permissions before registering the native host.

#### Scenario: All prerequisites are present
- **WHEN** the user runs setup on macOS with every required dependency available
- **THEN** setup reports the resolved executable paths and proceeds to registration

#### Scenario: A prerequisite is missing
- **WHEN** setup cannot resolve a required dependency
- **THEN** it exits without a partial registration and prints an actionable installation instruction

### Requirement: Native host registration
The setup workflow SHALL accept and validate the unpacked extension identifier, create an absolute executable launcher, and install a native-host manifest in Chrome's per-user macOS NativeMessagingHosts directory with only that extension in `allowed_origins`.

#### Scenario: Fresh installation succeeds
- **WHEN** the user supplies a valid Chrome extension identifier
- **THEN** Chrome can resolve the installed native host and only the specified extension origin is authorized

#### Scenario: Invalid extension identifier is supplied
- **WHEN** the supplied identifier is not a valid Chrome extension identifier
- **THEN** setup rejects it before writing the native-host manifest

### Requirement: Repeatable setup and removal
Installation SHALL be safe to rerun for the same checkout and extension identifier, and the project SHALL provide instructions or tooling that removes only files installed by this host.

#### Scenario: Setup is rerun
- **WHEN** the registered checkout has moved or dependencies have changed
- **THEN** rerunning setup refreshes the launcher and manifest without creating duplicate registrations

#### Scenario: User removes the host
- **WHEN** the documented removal workflow is run
- **THEN** it removes the host's manifest and generated launcher without deleting downloaded videos or unrelated files

### Requirement: End-to-end diagnostics
The setup workflow SHALL provide a diagnostic check that validates manifest contents and executable paths, and the extension SHALL expose an actionable native-host connection failure.

#### Scenario: Registration is healthy
- **WHEN** diagnostics run after successful setup
- **THEN** they confirm that the manifest, allowed extension origin, launcher, Python, `yt-dlp`, and `ffmpeg` are usable

#### Scenario: Extension cannot connect
- **WHEN** Chrome reports that the native host is missing, forbidden, or exits before replying
- **THEN** the popup distinguishes setup failure from media-discovery failure and points the user to the diagnostic workflow
