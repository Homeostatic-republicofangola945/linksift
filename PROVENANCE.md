# Project provenance

This document records where LinkSift started, what the independent project has changed, and where its public history and adoption metrics begin.

## Verified source baseline

LinkSift began from source code published as [ReClip by Avery Gan](https://github.com/averygan/reclip). The baseline incorporated into the project was upstream commit [`1d161d15a4fe93d9b3371377f0a421dc3e965b10`](https://github.com/averygan/reclip/commit/1d161d15a4fe93d9b3371377f0a421dc3e965b10), dated July 10, 2026.

At that revision, the upstream root license was the MIT License with the notice `Copyright (c) 2026` and no named copyright holder. The notice is preserved verbatim in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No upstream endorsement, sponsorship, or continuing affiliation is implied.

## Independent LinkSift work

LinkSift is maintained independently and has substantially changed the source baseline, including:

- a complete LinkSift identity and responsive light/dark interface;
- safer input, output-path, filename, and runtime validation;
- bounded concurrency, real process-tree cancellation, timeout handling, and job/file TTL cleanup;
- bounded playlist expansion with malformed-entry handling and explicit truncation reporting;
- a non-root Docker/Gunicorn runtime and local-only default network binding;
- an offline regression suite, continuous integration, release automation, multi-architecture images, and build attestations;
- rewritten end-user, security, contributor, release, and project-governance documentation.

New LinkSift modifications are distributed under the project's [MIT License](LICENSE), with copyright held by iaht. Inherited portions remain subject to the upstream MIT notice.

## History and metrics boundary

The public LinkSift repository starts with commit [`484fe5adcf8e77db0a9bf3754ad3e7d931b0855b`](https://github.com/loveisbl1nd/linksift/commit/484fe5adcf8e77db0a9bf3754ad3e7d931b0855b) and maintains its own releases, issues, pull requests, contributors, stars, forks, package downloads, and other adoption signals. LinkSift does not claim activity or adoption belonging to the upstream repository.

## Audit record

This record was checked on August 14, 2026 against the upstream commit and its root `LICENSE` file. If a future source import is made, add its repository, exact commit, license, and the scope of the imported changes here before release.
