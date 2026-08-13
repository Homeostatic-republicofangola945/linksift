# LinkSift

**Self-hosted media downloader powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp).** Paste links from supported sites and save MP4 video or MP3 audio through a focused local web interface.

> LinkSift is intended for personal, authorized use. Respect copyright law, platform terms, and creators' rights. It does not support DRM circumvention or bypassing access controls.

## Features

- Download MP4 video or extract MP3 audio from [yt-dlp-supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- Pick available video quality and download several links in sequence
- Live per-stream progress, speed, and ETA
- Optional destination-folder picker in Chromium browsers, with normal browser-save fallback
- Download timeout and concurrency controls
- Docker image runs as an unprivileged user and Compose binds to localhost by default

## Quick start

Install Docker Desktop, then run:

```bash
docker compose up --build
```

Open <http://localhost:8899>. LinkSift is bound to your local machine by default and needs no host Python, yt-dlp, or ffmpeg installation.

Downloads persist in the `linksift-downloads` Docker volume.

## Development

Local development is for contributors. Install Python 3.12, yt-dlp, and ffmpeg, then run:

```bash
./linksift.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor workflow.

## Configuration

| Variable | Default | Description |
| --- | ---: | --- |
| `PORT` | `8899` | HTTP port used by the development server. |
| `HOST` | `127.0.0.1` | Development-server bind address. Keep this local unless protected by a reverse proxy. |
| `LINKSIFT_DOWNLOAD_TIMEOUT` | `3600` | Maximum seconds for one yt-dlp process. |
| `LINKSIFT_MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum simultaneous downloads in the in-memory worker. |
| `LINKSIFT_NO_UPDATE` | unset | Set to `1` to prevent the startup script/container from updating yt-dlp. |

Job state lives in memory, so use the included one-worker Gunicorn configuration. Restarting the service clears active job status. Do not add workers until job state is moved to shared storage.

## Security and network exposure

LinkSift accepts URLs for yt-dlp to process and has no built-in authentication. **Do not expose it directly to the internet or an untrusted LAN.** If remote access is required, place it behind a reverse proxy with TLS, authentication, rate limiting, and egress controls that you operate.

## Validation

Tests are deliberately offline and mock external download tools. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Contributing

Bug reports, documentation improvements, tests, and focused pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the issue templates before contributing.

## License

[MIT](LICENSE)
