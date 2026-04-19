# syntax=docker/dockerfile:1.7

# ---------- builder ----------
FROM python:3.12-slim-bookworm AS builder

# Pull uv binary from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 1) Resolve and install dependencies without the project itself — this
#    layer caches on changes to pyproject.toml / uv.lock only.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) Copy the project source and install it into the venv so that the
#    `telegram-assistant` console script is created.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime ----------
FROM python:3.12-slim-bookworm AS runtime

# ffmpeg is required by yt-dlp for merging A/V streams on most sites.
# ca-certificates keeps HTTPS calls (LLM, enrichment, yt-dlp) working.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with a stable UID/GID for host-side permissions on /data.
RUN groupadd -g 1000 app \
    && useradd -r -u 1000 -g app -d /app -s /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /data

WORKDIR /app

# Virtualenv and source code from the builder.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src /app/src

# Config template and entrypoint.
COPY --chown=app:app config.example.toml /app/config.example.toml
COPY --chown=app:app entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

VOLUME ["/data"]

ENTRYPOINT ["/app/entrypoint.sh"]
