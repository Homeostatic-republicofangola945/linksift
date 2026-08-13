# LinkSift roadmap

LinkSift is a local-first, self-hosted utility. The roadmap favors reliability, understandable behavior, and a low-friction contributor experience over turning the project into a hosted platform.

## Project principles

- Keep the default deployment local and private: no accounts, telemetry, or cloud dependency.
- Make downloads bounded, cancellable, observable, and easy to clean up.
- Keep tests deterministic and offline; external-site checks belong in documented manual smoke tests.
- Preserve a small deployment surface: one container, one named volume, and one supported worker model.
- Accept focused changes that can be reviewed, tested, documented, and safely maintained.

## v0.1 — Reliable distribution

Available in the current source tree:

- [x] bounded download concurrency, cancellation, timeouts, and TTL cleanup;
- [x] playlist safety limits and malformed-entry handling;
- [x] offline unit and regression tests;
- [x] tag-driven GitHub Releases and multi-architecture GHCR images;
- [x] OCI metadata, SBOM generation, and verifiable build provenance;
- [x] explicit source provenance and third-party license notices.

Release milestone:

- [ ] publish and verify the first `v0.1.0` release;
- [ ] validate the published image on Linux amd64, Linux arm64, Windows Docker Desktop, and macOS Docker Desktop;
- [ ] record a short, repeatable manual smoke-test checklist for common download flows.

## v0.2 — Diagnostics and compatibility

Good contribution candidates:

- expose clearer disk-space and write-permission diagnostics before a large download starts;
- add structured, privacy-conscious runtime logs with stable job identifiers;
- expand cancellation and cleanup tests around process-tree and container shutdown edge cases;
- document a small compatibility matrix without making live platform calls part of CI;
- improve keyboard navigation, focus states, status announcements, and reduced-motion behavior;
- make startup dependency/update failures visible in the UI without leaking internal paths.

## v0.3 — Maintainability and contributor experience

Larger proposals that should start with an issue:

- separate download lifecycle, yt-dlp command construction, and HTTP handlers into testable modules;
- add browser-level tests for the queue while preserving the fast offline unit suite;
- define a deliberate dependency update and rollback policy;
- evaluate localization only after user-facing strings have a stable structure;
- document extension points for new output profiles without exposing arbitrary shell arguments.

## Non-goals

The project is not planning to become:

- a public hosted downloader, multi-tenant service, or commercial SaaS backend;
- a DRM circumvention or access-control bypass tool;
- a media archive, library manager, or permanent job database;
- a telemetry or user-tracking system;
- a compatibility promise for every site listed by yt-dlp.

## How to contribute to the roadmap

Small documentation, accessibility, and regression-test improvements can go directly to a focused pull request. For changes to architecture, dependencies, network exposure, persisted state, or supported output behavior, open a feature request first and include the user problem, proposed scope, security implications, and an offline testing plan.

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and validation commands. Roadmap placement communicates direction, not a deadline or guarantee that a proposal will be accepted unchanged.
