#!/bin/sh
set -eu
cd "$(dirname "$0")"

for tool in python3 yt-dlp ffmpeg; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf '%s\n' "Missing required development tool: $tool" >&2
        printf '%s\n' 'For normal use, start LinkSift with: docker compose up --build' >&2
        exit 1
    fi
done

if [ ! -d venv ]; then
    python3 -m venv venv
fi
. venv/bin/activate
pip install -q -r requirements.txt

if [ -z "${LINKSIFT_NO_UPDATE:-}" ]; then
    pip install -q -U yt-dlp || printf '%s\n' 'yt-dlp update failed; using installed version.' >&2
fi

exec python3 app.py
