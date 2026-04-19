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

## Manual smoke flow

1. In your Telegram client, open any chat and type `/draft`. Pause briefly.
2. The input field should update to a generated draft.
3. Add a chat to auto-draft with `/auto on` (in that chat's input).
4. Type `/fix hi how ar you`. The input field should update to the corrected text.
5. Paste an Instagram URL into a chat listed under `modules.media_reply.chats`. The bot should reply with the downloaded media.
