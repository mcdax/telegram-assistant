# Docker Support — Design

**Date:** 2026-04-19
**Status:** Draft for review

## 1. Purpose

Ship `telegram-assistant` as a container image plus a one-command bring-up via `docker compose`. The deployment target is a single host (home-lab, VPS, or developer machine). Users who prefer plain `docker run` can use the image directly; Compose is the ergonomic front-door.

## 2. Scope

- `Dockerfile` — reproducible image build with `uv`, slim Python base, `ffmpeg` bundled for `yt-dlp`, non-root runtime user.
- `docker-compose.yml` — single-service definition with a persistent data volume, `.env` for secrets, `restart: unless-stopped`.
- `.dockerignore` — exclude local state, tests, docs, virtualenvs, git history.
- `.env.example` — template for LLM provider credentials (e.g. `ANTHROPIC_API_KEY=...`).
- **First-run config bootstrap:** when the container starts and `/data/config.toml` does not exist, copy a **commented template** into `/data/`, log an explicit instruction, and exit non-zero so the user notices. Users edit the file on the host and restart.
- Documentation update in `README.md` covering initial Telethon login (interactive), then normal detached operation.

Out of scope: Kubernetes manifests, prebuilt images published to a registry, sidecar/enrichment service, arm64 cross-builds (`buildx` can still be used — we just don't add a Makefile for it), runtime hot-reload beyond what `config_loader` already does.

## 3. Image

### 3.1 Base and build strategy

Two-stage build:

1. **Builder stage** — `python:3.12-slim` with `uv` installed. Copies `pyproject.toml`, `uv.lock`, `src/`. Runs `uv sync --frozen --no-dev` into a project-local `.venv`.
2. **Runtime stage** — `python:3.12-slim`. Installs `ffmpeg` via `apt-get` (and `ca-certificates`). Copies the virtualenv and `src/` from the builder. Adds a non-root user `app` (UID 1000), makes `/data` owned by it, switches to it.

Why `slim` over `alpine`: `yt-dlp` + `ffmpeg` on alpine requires a musl-compatible ffmpeg build and often breaks on edge-case sites; debian-slim just works. Size difference is small once `ffmpeg` is in the picture.

Why two-stage: keeps `uv`, build tools, and the wheel cache out of the final image. Also makes `docker build` cache reliably on dependency changes (copy `pyproject.toml`/`uv.lock` before `src/`).

### 3.2 Runtime layout

```
/app                        ← read-only application code
  /app/src/telegram_assistant/...
  /app/.venv/               ← virtualenv from builder stage
  /app/config.example.toml  ← the commented template (used by first-run bootstrap)
  /app/entrypoint.sh        ← first-run bootstrap + exec

/data                       ← bind-mounted writable volume
  /data/config.toml         ← user-authored; auto-seeded on first run
  /data/state.toml          ← app-managed (per-chat auto-draft overrides)
  /data/<session>.session   ← Telethon session
```

Working directory: `/app`.
`PATH` is set so `telegram-assistant` from the venv is the default command.

### 3.3 Entrypoint script

`/app/entrypoint.sh`:

```sh
#!/bin/sh
set -eu

if [ ! -f /data/config.toml ]; then
  echo "No /data/config.toml found — writing a commented template."
  cp /app/config.example.toml /data/config.toml
  echo "Edit /data/config.toml with your Telegram and LLM credentials, then restart the container."
  exit 2
fi

exec telegram-assistant \
  --config /data/config.toml \
  --state /data/state.toml \
  --log-level "${LOG_LEVEL:-INFO}"
```

Exit code `2` is deliberately distinct from `0` (success) and `1` (app error) so users can script around "config missing" if they want. Under `docker compose run --rm` (the recommended first invocation in §5) the restart policy is ignored, so the container exits cleanly after seeding. Under `docker compose up -d` with an empty `/data`, Compose's `restart: unless-stopped` will repeatedly re-spawn the container and each start re-executes the idempotent `! -f` seed check; this just spams logs until the user populates `config.toml`.

### 3.4 CMD vs ENTRYPOINT

`ENTRYPOINT ["/app/entrypoint.sh"]` with no `CMD`. Keeps the bootstrap check inescapable under normal use, while still allowing `docker run --entrypoint sh ...` for debugging.

### 3.5 `config.example.toml` with comments

The existing `config.example.toml` is the seed for first-run. It will be enriched with explanatory comments on every field:

- `[telegram]` block: where to get `api_id` / `api_hash`, what `session` names the session file.
- `[llm]` block: supported model slug formats, which env var provides the API key per provider.
- `[modules.drafting]` block: what `default_system_prompt`, `last_n`, `auto_draft_chats` mean; explicitly note that `state.toml` overrides the whitelist at runtime.
- `[modules.drafting.markers]` / `[modules.correcting.markers]`: how to change the trigger strings.
- `[modules.media_reply]` block: chat-whitelist safety note (empty list = module inactive), how to add handlers.

Comments are `#` lines preceding each field. Any comment addition is a pure-docs change; runtime behaviour is unchanged.

## 4. `docker-compose.yml`

```yaml
services:
  telegram-assistant:
    build: .
    image: telegram-assistant:latest
    container_name: telegram-assistant
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/data
    stdin_open: true
    tty: true
```

- `stdin_open + tty` are both true so the **first-run** interactive Telethon login (phone number, login code, 2FA password) works via `docker compose run --rm telegram-assistant`. Harmless for background operation.
- `env_file: .env` — user copies `.env.example` → `.env` and fills in `ANTHROPIC_API_KEY` (or whichever provider).
- `container_name` fixed so `docker logs telegram-assistant` works without lookup.

No ports published — the app does not listen on any.

## 5. Operator flow

1. `cp .env.example .env` and fill in the LLM credentials.
2. `docker compose build`.
3. `docker compose run --rm telegram-assistant` — exits with a message and writes `./data/config.toml`.
4. Edit `./data/config.toml` (fill in Telegram `api_id`, `api_hash`, enrichment URL, whitelists).
5. `docker compose run --rm telegram-assistant` again — Telethon prompts interactively for phone/code, saves the session file under `./data/`.
6. `docker compose up -d` — normal detached operation.
7. `docker compose logs -f` to follow output.
8. To change config: edit `./data/config.toml` on the host. `watchfiles` picks it up inside the container (the current `__main__.py` logs a reload notice; full hot-reload is documented as a future item in the original spec).

## 6. `.dockerignore`

```
# Secrets and local state
config.toml
state.toml
.env
*.session
*.session-journal
data/

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/

# Git / editor / docs not needed in the image
.git/
.gitignore
.vscode/
.idea/
docs/
tests/
```

Excluding `tests/` keeps the image lean; tests are still runnable on the host via `uv run pytest`. `docs/` excluded for the same reason.

## 7. Error handling

| Failure                                       | Behaviour                                                                                      |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| `/data/config.toml` missing                   | Entrypoint seeds it from `config.example.toml`, logs instruction, exits 2. User edits, restarts. |
| `/data/config.toml` invalid                   | `load_config` raises `ConfigError`, process exits non-zero, Compose restarts per policy. User sees the error in `docker logs`. |
| Telethon login aborted (no TTY attached on first run) | Process exits. User re-runs with `docker compose run --rm` to get interactive shell. |
| `/data` not writable                          | First-run copy fails; entrypoint exits with error. User fixes host-side permissions.           |
| LLM creds missing                             | Pydantic AI raises at first LLM call; the individual drafting run logs and writes no draft. The container keeps running. |

## 8. Testing

This work is primarily packaging. Verification is manual:

1. `docker compose build` succeeds on a clean tree.
2. `docker compose run --rm telegram-assistant` on an empty `./data/` creates a `config.toml` and exits with code 2, not 0.
3. Rerunning without editing does NOT exit 2 — the `! -f` check sees the seeded file and proceeds to the app, which fails with `ConfigError` on dummy `api_id = 0`. The seed is therefore a one-shot behaviour, not a repeated warning.
4. With a valid `config.toml` and `.env`, `docker compose up -d` starts the container, `docker logs` shows the assistant connected to Telegram (or prompts interactively on first real run).
5. `uv run pytest` on the host still passes (no production code changed beyond `config.example.toml` comments).

A smoke check is added to `README.md` operator notes. No new automated tests — containers are not unit-testable without real Telegram.

## 9. Non-goals and trade-offs

- **No registry publish.** If `docker pull` becomes desirable, add a `ci`/release step later; today the image is built locally from source.
- **No distroless or alpine experiment.** `ffmpeg` + yt-dlp compatibility and debugability outweigh the image-size saving.
- **No multi-arch build.** `docker build` on your host produces a single-arch image. `buildx` works if you need it, but we don't wire it into Compose.
- **No healthcheck.** Telethon has no HTTP surface. `restart: unless-stopped` covers process crashes; deeper health signals are out of scope.
- **State in one volume.** Splitting `config.toml` (read-mostly) from `state.toml` / `*.session` (writable) would be marginally cleaner but every backup script would now need two paths. Single `/data` wins on simplicity.

## 10. Open items

- Exact wording of the first-run message (keep it terse; mention both the file path and what the user must fill in).
- Whether `state.toml` should also be seeded empty on first run (arguably unnecessary — `RuntimeState._load` handles a missing file by returning `{}` and writes on first mutation).
