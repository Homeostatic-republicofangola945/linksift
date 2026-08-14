# Deno supplies the JavaScript runtime yt-dlp needs to solve YouTube's JS
# challenges (EJS). Official image, pinned; only the static binary is copied.
FROM denoland/deno:bin-2.4.3 AS deno

FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="LinkSift" \
      org.opencontainers.image.description="Local-first media download queue powered by yt-dlp and ffmpeg" \
      org.opencontainers.image.source="https://github.com/loveisbl1nd/linksift" \
      org.opencontainers.image.documentation="https://github.com/loveisbl1nd/linksift#readme" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && \
    useradd -m -u 1000 linksift && \
    mkdir -p /app/downloads && \
    chown -R linksift:linksift /app
USER linksift

# Put the linksift user's --user installs first so startup yt-dlp updates take effect.
ENV PATH=/home/linksift/.local/bin:$PATH

EXPOSE 8899

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["gunicorn", "-b", "0.0.0.0:8899", "-w", "1", "--threads", "4", "--timeout", "600", "--access-logfile", "-", "app:app"]

# Optional target with the GPL-licensed PO token provider plugin. Its version
# must stay in lockstep with the provider sidecar image, so the startup
# updater intentionally leaves it alone.
FROM base AS youtube-robust
USER root
RUN pip install --no-cache-dir -r /app/requirements-youtube-robust.txt
USER linksift

# Default build target: the base image without the GPL plugin.
FROM base AS default
