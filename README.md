# LinkSift

<p align="center">
  <img src="static/favicon.svg" alt="LinkSift" width="72">
</p>

<p align="center">
  <strong>Turn links into a tidy local queue.</strong><br>
  Inspect, format, and save media from a focused self-hosted web workspace.
</p>

<p align="center">
  <a href="https://github.com/loveisbl1nd/linksift/actions/workflows/ci.yml"><img src="https://github.com/loveisbl1nd/linksift/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-c8f55a?labelColor=10171b" alt="MIT License"></a>
  <a href="https://github.com/yt-dlp/yt-dlp"><img src="https://img.shields.io/badge/powered%20by-yt--dlp-10171b" alt="Powered by yt-dlp"></a>
  <a href="Dockerfile"><img src="https://img.shields.io/badge/runtime-Docker-2496ED?logo=docker&logoColor=white" alt="Docker runtime"></a>
</p>

LinkSift is a local-first media downloader powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) and ffmpeg. Paste one or more supported URLs, inspect the available metadata, choose MP4 or MP3, and follow each download from the same queue.

> Built for personal, authorized use. Respect copyright law, platform terms, and creators' rights. LinkSift does not support DRM circumvention or bypassing access controls.

## At a glance

| | |
| --- | --- |
| **Deployment** | Docker Compose for normal use; local launcher for contributors |
| **Interface** | Responsive browser UI with light, dark, and system themes |
| **Formats** | MP4 video or MP3 audio |
| **Queue** | Multiple URLs, quality selection, concurrency limit, live progress |
| **Runtime** | Python + Flask, yt-dlp, ffmpeg, Gunicorn, non-root container |
| **Privacy model** | Local by default; no built-in account, telemetry, or public service |

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="assets/screenshot-home.png" alt="LinkSift light theme desktop workspace"></td>
    <td width="50%"><img src="assets/screenshot-dark.png" alt="LinkSift dark theme desktop workspace"></td>
  </tr>
  <tr>
    <td align="center"><sub>Light theme - desktop workspace</sub></td>
    <td align="center"><sub>Dark theme - desktop workspace</sub></td>
  </tr>
</table>

<p align="center">
  <img src="assets/screenshot-mobile.png" alt="LinkSift responsive mobile workspace" width="330"><br>
  <sub>Responsive mobile layout</sub>
</p>

## The workflow

<table>
  <tr>
    <td width="33%"><strong>01 - Inspect</strong><br><br>Paste a URL or a batch of URLs. LinkSift asks yt-dlp for metadata without downloading the media first.</td>
    <td width="33%"><strong>02 - Choose</strong><br><br>Pick MP4 or MP3, then select an available video quality when the source provides one.</td>
    <td width="33%"><strong>03 - Collect</strong><br><br>Watch progress, speed, and ETA. Save completed files through the browser or an optional folder picker.</td>
  </tr>
</table>

## What is included

- **Local-first by design** - Compose binds to `127.0.0.1:8899` by default.
- **Batch-friendly queue** - paste one or more supported URLs and process them in sequence.
- **MP4 and MP3 output** - choose a preferred format before inspection.
- **Quality selection** - choose from the available video heights returned by yt-dlp.
- **Live progress** - phase, percentage, downloaded bytes, speed, ETA, and final status.
- **Browser save controls** - use the default browser download flow or choose a folder in Chromium-based browsers.
- **Predictable runtime** - Docker includes Python, yt-dlp, ffmpeg, Gunicorn, and a non-root `linksift` user.
- **Offline CI** - regression tests mock external tools and never call media platforms.

## Quick start

Docker is the supported end-user path. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then run:

```bash
docker compose up --build
```

Open <http://localhost:8899>. You do not need Python, yt-dlp, ffmpeg, or a virtual environment on the host.

Downloads persist in the named `linksift-downloads` Docker volume. To stop the service, press `Ctrl+C`; to run it in the background, use `docker compose up --build -d`.

## Development

The local launcher is for contributors and requires Python 3.12, yt-dlp, and ffmpeg:

```bash
./linksift.sh
```

Before opening a pull request, run:

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py
docker compose config
docker build -t linksift:local .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow and pull request checklist.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `PORT` | `8899` | HTTP port used by the development server. |
| `HOST` | `127.0.0.1` | Bind address. Keep it local unless a protected reverse proxy is in front. |
| `LINKSIFT_DOWNLOAD_TIMEOUT` | `3600` | Maximum seconds allowed for one yt-dlp process. |
| `LINKSIFT_MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum simultaneous downloads in the in-memory worker. |
| `LINKSIFT_NO_UPDATE` | unset | Set to `1` to skip the startup yt-dlp update. |

Job state is held in memory. The Docker command therefore uses one Gunicorn worker; restarting the service clears active job status. Do not add workers until job state moves to shared storage.

## Supported sites

LinkSift accepts the sites supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including YouTube, TikTok, Instagram, Reddit, Facebook, Vimeo, Twitch, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

The supported-site list changes with yt-dlp releases. LinkSift updates yt-dlp at container startup by default; set `LINKSIFT_NO_UPDATE=1` to opt out.

## Security and network exposure

LinkSift accepts URLs for yt-dlp to process and has no built-in authentication. **Do not expose it directly to the internet or an untrusted LAN.** If remote access is required, place it behind a reverse proxy with TLS, authentication, rate limiting, and egress controls that you operate.

For a vulnerability report, use [GitHub Private Vulnerability Reporting](https://github.com/loveisbl1nd/linksift/security/advisories/new) instead of opening a public issue. See [SECURITY.md](SECURITY.md) for the disclosure policy.

## Project layout

```text
app.py                 Flask API, queue state, and download worker
templates/index.html   Responsive browser interface
static/                Favicon and static assets
assets/                README screenshots
Dockerfile             Production container image
docker-compose.yml     Local Docker deployment
linksift.sh            Contributor-only local launcher
tests/                 Offline regression suite
.github/               CI, issue forms, and pull request template
```

## Contributing

Bug reports, documentation improvements, tests, and focused pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the issue templates before contributing.

## License

[MIT](LICENSE) - Copyright (c) 2026 iaht
