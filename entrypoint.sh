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
