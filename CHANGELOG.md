# Changelog

All notable changes to LinkSift are documented here.

## [Unreleased]

### Added

- LinkSift public-project baseline: contribution guide, security policy, issue forms, pull-request template, and CI.
- Input validation, safe output selection, filename sanitization, and bounded concurrent downloads.
- Automatic lifecycle cleanup: finished jobs and their files expire after `LINKSIFT_JOB_TTL` seconds (default 24 hours), with orphan-file sweeps at startup and during normal request activity.
- Server-side download cancellation via `DELETE /api/download/<job_id>`, including process-tree termination, partial-file cleanup, and a Cancel button on active queue cards.
- Configurable playlist expansion limit via `LINKSIFT_MAX_PLAYLIST_ITEMS` (default 200); `/api/playlist` now reports `truncated` and `limit`, and the UI shows a gentle truncation notice.

### Changed

- Rebranded from the inherited project identity to LinkSift.
- Hardened progress polling, browser folder saving, and frontend metadata rendering.

### Fixed

- Playlist truncation is now detected from the raw yt-dlp entry count, so playlists containing unavailable entries still report `truncated` correctly; blank or malformed entries are skipped without failing the request.
