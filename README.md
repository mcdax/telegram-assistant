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

1. In your Telegram client, open any chat and type `/draft`. Pause briefly.
2. The input field should update to a generated draft.
3. Add a chat to auto-draft with `/auto on` (in that chat's input).
4. Type `/fix hi how ar you`. The input field should update to the corrected text.
5. Paste an Instagram URL into a chat listed under `modules.media_reply.chats`. The bot should reply with the downloaded media.
