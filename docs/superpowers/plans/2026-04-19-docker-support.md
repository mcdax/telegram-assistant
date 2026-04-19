# Docker Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `telegram-assistant` as a Docker image plus a one-command `docker compose up` bring-up, with a first-run entrypoint that seeds a commented `config.toml` template for the user to edit.

**Architecture:** Multi-stage Dockerfile (`python:3.12-slim-bookworm` + `uv`) producing a slim runtime image with `ffmpeg` bundled for `yt-dlp`. A shell entrypoint bootstraps `/data/config.toml` from a commented `config.example.toml` on first run and exits non-zero, otherwise execs the CLI. `docker-compose.yml` wires up a single `./data:/data` bind-mount, `.env` for LLM provider secrets, `restart: unless-stopped`, and an interactive TTY for the first-run Telethon login.

**Tech Stack:** Docker, docker compose v2, `python:3.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.5` (build-time), `apt` (ffmpeg, ca-certificates), POSIX `sh` for entrypoint.

**Spec:** `docs/superpowers/specs/2026-04-19-docker-support-design.md`.

---

## File Layout (target)

```
telegram-assistant/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── entrypoint.sh
└── config.example.toml   (modified — commented)
└── README.md             (modified — operator section)
```

No production source code under `src/` changes. No test files change. Verification is manual (`docker compose build`, `docker compose run --rm`, `docker compose up`). The existing `uv run pytest` suite (81 tests) must remain green because `pyproject.toml` is unchanged.

---

## Task 1: Enrich `config.example.toml` with explanatory comments

Seed-template quality matters: the user will read this file after the first container run. Turn the sparse example into a self-documenting config.

**Files:**
- Modify: `config.example.toml` (full rewrite, same keys/values)

- [ ] **Step 1: Rewrite `config.example.toml`**

Replace the entire file with:

```toml
# telegram-assistant — configuration
#
# This file is consumed at startup. On first run inside the container, an
# empty /data volume causes the entrypoint to seed this template for you;
# fill in the placeholders below and restart.
#
# Secret values (api_id, api_hash, LLM API keys, enrichment auth header)
# should be treated as credentials — do not commit this file to version
# control.

[telegram]
# Your Telegram API credentials. Obtain them at https://my.telegram.org
# → API development tools. One pair per Telegram account.
api_id = 0
api_hash = "YOUR_API_HASH"

# Telethon stores the login session under this name (e.g. "assistant.session")
# next to the config file. Keep it stable across restarts to avoid re-login.
session = "assistant"


[llm]
# Any model slug pydantic-ai recognises: "anthropic:claude-sonnet-4-6",
# "openai:gpt-4o", "google-gla:gemini-1.5-pro", "ollama:llama3.1", etc.
# The matching API key is read from the provider's environment variable
# (for example ANTHROPIC_API_KEY) — put it in .env, not here.
model = "anthropic:claude-sonnet-4-6"

# Per-call timeout. Generated drafts that exceed this are silently skipped
# and logged; your Telegram input stays untouched.
timeout_s = 30


# ---------- Module: drafting ----------
# Drafts replies in your voice using the chat history, an optional external
# enrichment endpoint (calendar/email/notes), and the LLM. Owns the /draft
# marker and the /auto on|off runtime toggles for auto-drafting.
[modules.drafting]
enabled = true

# Baseline system prompt for reply drafts. Can be overridden per chat
# under [modules.drafting.chats."<chat_id>"].
default_system_prompt = "You are drafting a reply in the user's voice. Casual, concise, matches the conversation's tone."

# Number of recent messages fetched and shown to the LLM. 20 is a good
# default; bump it per-chat for long-running threads.
last_n = 20

# Initial whitelist of chat IDs where incoming messages trigger auto-draft.
# This is a seed only — the runtime truth lives in state.toml and is
# toggled with /auto on / /auto off in-chat.
auto_draft_chats = []

# Optional external enrichment service. When set, each draft POSTs the
# last-N messages and uses the response as extra context. Leave empty to
# draft from chat history alone.
enrichment_url = ""
enrichment_auth_header = ""
enrichment_timeout_s = 10

# Trigger strings for this module's markers. Keys are the stable internal
# names; values are whatever you want to type in your Telegram input.
[modules.drafting.markers]
draft    = "/draft"
auto_on  = "/auto on"
auto_off = "/auto off"

# Optional per-chat overrides. Positive IDs (users) use bare keys; negative
# IDs (supergroups/channels) MUST be quoted.
# [modules.drafting.chats."12345"]
# system_prompt = "Professional tone. Keep replies brief."
# last_n = 50
#
# [modules.drafting.chats."-1001234567890"]
# system_prompt = "Team channel — be terse and technical."


# ---------- Module: correcting ----------
# Rewrites your own text for grammar / spelling / punctuation on the /fix
# marker. No chat history, no enrichment.
[modules.correcting]
enabled = true
system_prompt = "Rewrite the given text correcting grammar, spelling, and punctuation. Preserve meaning, tone, and language. Output only the rewritten text."

[modules.correcting.markers]
fix = "/fix"


# ---------- Module: media_reply ----------
# When an incoming message contains a URL matching a handler's regex, the
# module downloads the media with the configured backend and replies with
# the file. Requires ffmpeg for most sites (already bundled in the image).
[modules.media_reply]
enabled = true

# Safety default — an empty whitelist means the module does nothing. Add
# the chat IDs where you want URL-aware replies.
chats = []

# "reply" = send the media as a reply to the original message. "draft" is
# reserved for a future release.
send_as = "reply"
download_timeout_s = 60

# One handler block per URL family. Add more by repeating the section.
[[modules.media_reply.handlers]]
name = "instagram"
pattern = 'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+'
backend = "yt_dlp"

# Example: add TikTok too.
# [[modules.media_reply.handlers]]
# name = "tiktok"
# pattern = 'https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+'
# backend = "yt_dlp"
```

- [ ] **Step 2: Verify TOML is still valid**

```bash
uv run python -c "import tomllib; tomllib.load(open('config.example.toml', 'rb')); print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Run the full test suite — it must still pass**

```bash
uv run pytest --tb=no -q
```

Expected: `81 passed`. (No production code changed; this is a sanity check.)

- [ ] **Step 4: Commit**

```bash
git add config.example.toml
git commit -m "docs(config): annotate config.example.toml for first-run operator"
```

---

## Task 2: `.dockerignore` and `.env.example`

Keep secrets and local state out of the build context; give the user a copy-and-edit template for LLM API keys.

**Files:**
- Create: `.dockerignore`
- Create: `.env.example`

- [ ] **Step 1: Write `.dockerignore`**

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

# Git / editor / docs / tests not needed in the image
.git/
.gitignore
.vscode/
.idea/
docs/
tests/
```

- [ ] **Step 2: Write `.env.example`**

```
# Copy to .env and fill in the API key(s) for the LLM provider(s) you use.
# Only the provider matching [llm].model in config.toml is actually read.

# Anthropic (anthropic:claude-sonnet-4-6 etc.)
ANTHROPIC_API_KEY=

# OpenAI (openai:gpt-4o etc.)
# OPENAI_API_KEY=

# Google (google-gla:gemini-1.5-pro etc.)
# GOOGLE_API_KEY=

# Mistral, Groq, etc. — add as needed. pydantic-ai reads the matching
# provider env var automatically.
```

- [ ] **Step 3: Commit**

```bash
git add .dockerignore .env.example
git commit -m "chore(docker): add .dockerignore and .env.example"
```

---

## Task 3: `entrypoint.sh`

POSIX shell script. Seeds `/data/config.toml` from the bundled template on first run and exits 2; otherwise execs the CLI with the correct paths.

**Files:**
- Create: `entrypoint.sh`

- [ ] **Step 1: Write `entrypoint.sh`**

```sh
#!/bin/sh
set -eu

CONFIG_PATH="/data/config.toml"
STATE_PATH="/data/state.toml"
TEMPLATE_PATH="/app/config.example.toml"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "No $CONFIG_PATH found — writing a commented template." >&2
  cp "$TEMPLATE_PATH" "$CONFIG_PATH"
  echo "Edit $CONFIG_PATH with your Telegram and LLM credentials, then restart the container." >&2
  exit 2
fi

exec telegram-assistant \
  --config "$CONFIG_PATH" \
  --state "$STATE_PATH" \
  --log-level "${LOG_LEVEL:-INFO}"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x entrypoint.sh
```

- [ ] **Step 3: Smoke-check shell syntax**

```bash
sh -n entrypoint.sh && echo ok
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add entrypoint.sh
git update-index --chmod=+x entrypoint.sh
git commit -m "feat(docker): add first-run entrypoint script"
```

Note: `git update-index --chmod=+x` records the executable bit in the tree so subsequent clones/builds preserve it even if the local filesystem loses the bit.

---

## Task 4: `Dockerfile`

Multi-stage build: builder installs deps with `uv` into `/app/.venv`, runtime copies the venv + `src/` onto a `slim` base with `ffmpeg`, creates a non-root `app` user, and sets the entrypoint.

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
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
```

- [ ] **Step 2: Build the image**

```bash
docker build -t telegram-assistant:dev .
```

Expected: build completes successfully. First build will pull the `uv` and `python:3.12-slim-bookworm` images, then install deps. Subsequent builds should be fast for source-only changes.

- [ ] **Step 3: Verify the image runs the entrypoint and has the CLI available**

```bash
docker run --rm --entrypoint telegram-assistant telegram-assistant:dev --help
```

Expected: argparse help text including `--config`, `--state`, `--log-level`. This proves the venv is on PATH and the console script is installed.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat(docker): add multi-stage Dockerfile with uv and ffmpeg"
```

---

## Task 5: `docker-compose.yml`

Single-service definition wired to `./data` bind-mount, `.env` for secrets, interactive TTY for first-run login, and `restart: unless-stopped` for steady-state operation.

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write `docker-compose.yml`**

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

- [ ] **Step 2: Validate compose file**

```bash
docker compose config > /dev/null && echo ok
```

Expected: `ok` (no YAML / schema errors; warnings about the missing `.env` are fine if they show up — that's expected at plan-time).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(docker): add docker-compose.yml for single-host deployment"
```

---

## Task 6: End-to-end smoke verification

Prove that the three real user flows work: empty-volume bootstrap, valid-config normal start, and the full suite still green.

**Files:** none changed (verification only).

- [ ] **Step 1: Prepare a clean test data directory**

```bash
rm -rf /tmp/tga-smoke && mkdir /tmp/tga-smoke
```

- [ ] **Step 2: Ensure `.env` exists for Compose**

```bash
cp -n .env.example .env || true
```

(`cp -n` only copies if `.env` doesn't already exist.)

- [ ] **Step 3: First run against an empty data dir — expect bootstrap + exit 2**

Use `docker run` directly (bypasses compose restart policy) with the test data dir:

```bash
docker run --rm -v /tmp/tga-smoke:/data telegram-assistant:dev; echo "exit=$?"
```

Expected: two log lines (bootstrap message + edit instruction), `exit=2`, and `/tmp/tga-smoke/config.toml` now exists as a copy of the commented template.

Verify the seeded file:

```bash
head -5 /tmp/tga-smoke/config.toml
```

Expected: leading lines beginning with `# telegram-assistant — configuration`.

- [ ] **Step 4: Second run without editing — expect ConfigError (dummy api_id = 0 is rejected)**

```bash
docker run --rm -v /tmp/tga-smoke:/data telegram-assistant:dev; echo "exit=$?"
```

Expected: the entrypoint detects the config, execs the CLI. The CLI itself attempts to connect with `api_id = 0` and will fail either at config validation (depending on how strict `load_config` is) or at Telethon connect. A non-zero exit is expected; what matters is that the bootstrap message does NOT reappear — confirming `entrypoint.sh` is idempotent as designed.

Expected log shape: no bootstrap message; either a `ConfigError` trace or a Telethon-level error about invalid credentials. **This step is informational** — failure here is expected and correct behaviour.

- [ ] **Step 5: Compose config validation with real `.env` path**

```bash
docker compose config | grep -E "(image|env_file|stdin_open|tty)"
```

Expected: lines showing `image: telegram-assistant:latest`, `env_file: .env`, `stdin_open: true`, `tty: true`.

- [ ] **Step 6: Full test suite still green**

```bash
uv run pytest --tb=no -q
```

Expected: `81 passed`. No production code changed; this catches accidental pyproject.toml or src/ damage.

- [ ] **Step 7: Cleanup**

```bash
rm -rf /tmp/tga-smoke
docker image rm telegram-assistant:dev
```

(Safe to skip if you want the image kept for manual testing.)

- [ ] **Step 8: No commit — this task is verification only.**

---

## Task 7: README operator section

Document the new workflow so users know about the interactive first-run login and the detached steady-state pattern.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current `README.md` to find where to append**

Before editing, confirm the file has a `## Manual smoke flow` section produced by the previous plan's Task 21. The new Docker section goes above it (after `## Tests`, before `## Manual smoke flow`).

- [ ] **Step 2: Insert a `## Docker` section before `## Manual smoke flow`**

Use the `Edit` tool. `old_string` is the line `## Manual smoke flow` (exact match in the current README). `new_string` is:

```markdown
## Docker

A multi-stage `Dockerfile` and `docker-compose.yml` are provided for single-host deployment. `ffmpeg` is bundled so `yt-dlp` works on all sites supported by the `media_reply` module.

### First-time setup

1. Copy secret templates:

   ```bash
   cp .env.example .env
   ```

   Fill in the LLM provider API key that matches `[llm].model` in your config (e.g. `ANTHROPIC_API_KEY`).

2. Build the image:

   ```bash
   docker compose build
   ```

3. Bootstrap the config template into `./data/`:

   ```bash
   docker compose run --rm telegram-assistant
   ```

   The container writes `./data/config.toml` from the commented template, prints an edit instruction, and exits with code 2.

4. Edit `./data/config.toml` — at minimum fill in `[telegram].api_id` / `api_hash` (from https://my.telegram.org) and any module-specific settings you care about (auto-draft whitelist, media-reply chats, enrichment URL).

5. Telethon login — interactive, one-time:

   ```bash
   docker compose run --rm telegram-assistant
   ```

   Telethon prompts for your phone number, login code, and (if enabled) 2FA password. The resulting `assistant.session` is saved to `./data/` and reused from then on. Ctrl-C once you see the assistant connect.

6. Normal operation — detached:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

### Updates

Pull / edit source, then:

```bash
docker compose build
docker compose up -d
```

`./data/` is untouched; config, state, and session persist across image rebuilds.

## Manual smoke flow
```

(Keep the existing `## Manual smoke flow` section unchanged below.)

- [ ] **Step 3: Verify the README renders cleanly**

```bash
grep -n "^##" README.md
```

Expected: headings appear in order — `## First run` (Host install), `## Tests`, `## Docker`, `## Manual smoke flow` — or whatever the existing layout was, with the new `## Docker` section present.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add Docker operator section"
```

---

## Self-review (author checklist)

**Spec coverage:**
- §2 scope items (Dockerfile, docker-compose.yml, .dockerignore, .env.example, first-run bootstrap, README) — covered by Tasks 1–7 (config template is Task 1, bootstrap is Task 3, README is Task 7).
- §3 image details (multi-stage, slim, ffmpeg, non-root, layout, entrypoint) — covered by Tasks 3 and 4.
- §3.5 `config.example.toml` with comments — covered by Task 1.
- §4 compose shape — covered by Task 5.
- §5 operator flow — covered by Task 7 (README).
- §6 `.dockerignore` — covered by Task 2.
- §7 error handling — behaviour is in `entrypoint.sh` (Task 3) and inherited from the existing app; verification in Task 6 Step 4 exercises the "re-run after partial edit" path.
- §8 testing (manual) — covered by Task 6.

**Placeholder scan:** no "TBD", "TODO", or vague directions. Every step contains exact content.

**Type / command consistency:**
- All references to `/app/config.example.toml`, `/app/entrypoint.sh`, `/data/config.toml`, `/data/state.toml`, `/app/.venv` use the same paths across tasks.
- `telegram-assistant:dev` is the locally built tag (Task 4 Step 2, Task 6 Step 3); `telegram-assistant:latest` is the Compose-named image (Task 5 Step 1). Both are documented — they can coexist because Compose also builds from the same `build: .` target.
- The Compose service name and container name are both `telegram-assistant` (Task 5).
- Entrypoint expects `telegram-assistant` on PATH (installed by `project.scripts` + `ENV PATH="/app/.venv/bin:$PATH"` in Task 4).
