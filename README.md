# telegram-assistant

Modular Telegram userbot. See `docs/superpowers/specs/2026-04-19-telegram-assistant-design.md` for design and `docs/superpowers/plans/2026-04-19-telegram-assistant.md` for the implementation plan.

## First run

1. Copy `config.example.toml` to `config.toml` and fill in:
   - `[telegram]` — `api_id`, `api_hash` from https://my.telegram.org
   - `[llm]` — a model slug supported by `pydantic-ai` (e.g. `anthropic:claude-sonnet-4-6`); set the matching API key in your environment.
2. `uv sync`
3. `uv run telegram-assistant --config config.toml --state state.toml`
   - On first run Telethon will prompt for your phone number and a login code.
4. The second run reuses the `*.session` file.

## Tests

```bash
uv run pytest
```

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

   Alternative: leave those placeholders in `config.toml` and set the credentials in `.env` instead (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, optionally `TELEGRAM_SESSION`, `LLM_MODEL`). The env vars override config values when both are set.

5. **Telethon login — interactive, one-time. Must use `run --rm`, not `up`.**

   ```bash
   docker compose run --rm telegram-assistant
   ```

   Telethon prompts for your phone number, login code, and (if enabled) 2FA password. The resulting `assistant.session` is saved to `./data/` and reused from then on. Ctrl-C once you see the assistant connect.

   > **Do not skip this step.** `docker compose up` (detached or attached) does not forward your terminal's stdin, so Telethon's first-time login prompt has nowhere to read the phone number from. The container will silently hang after `Connection complete!` with an empty session file. If that happened, run `docker compose down` and redo step 5.

6. Normal operation — detached:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

   This only works after step 5 has populated a valid `assistant.session`.

### Updates

Pull / edit source, then:

```bash
docker compose build
docker compose up -d
```

`./data/` is untouched; config, state, and session persist across image rebuilds.

### Troubleshooting

**Container logs stop at `Connection to …/TcpFull complete!` and nothing else happens.**
Telethon is waiting for an interactive login that `docker compose up` cannot provide. Fix:
```bash
docker compose down
docker compose run --rm telegram-assistant   # complete the login interactively, then Ctrl-C
docker compose up -d
```

**`sqlite3.OperationalError: unable to open database file` on startup.**
The host-side `./data` directory is not writable by UID 1000 (the `app` user inside the container). Fix ownership on the host:
```bash
sudo chown -R 1000:1000 ./data
```

**`ConfigError: missing telegram.api_id` (or `api_hash`) despite values in `config.toml`.**
`api_id = 0` and `api_hash = "YOUR_API_HASH"` are treated as unset placeholders so the first-run bootstrap fails loudly. Edit `./data/config.toml` with real values, or set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` in `.env`.

## Manual smoke flow

1. In your Telegram client, open any chat and type `/draft`. Pause briefly.
2. The input field should update to a generated draft.
3. Add a chat to auto-draft with `/auto on` (in that chat's input).
4. Type `/fix hi how ar you`. The input field should update to the corrected text.
5. Paste an Instagram URL into a chat listed under `modules.media_reply.chats`. The bot should reply with the downloaded media.
