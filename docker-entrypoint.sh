#!/bin/sh
# Keep yt-dlp and its EJS challenge scripts fresh on container start -- sites
# (YouTube, Instagram, Facebook, etc.) break extractors frequently, and the
# usual fix is simply updating them together.
# Installs into the linksift user's ~/.local (first on PATH). Skip with LINKSIFT_NO_UPDATE=1.
# The optional PO token plugin is intentionally NOT updated here: its version
# must stay in lockstep with the provider sidecar image.
if [ -z "$LINKSIFT_NO_UPDATE" ]; then
    echo "Updating yt-dlp and yt-dlp-ejs..."
    pip install --user --no-cache-dir -q -U yt-dlp yt-dlp-ejs || \
        echo "  (couldn't update yt-dlp/yt-dlp-ejs -- continuing with the installed versions)"
fi

exec "$@"
