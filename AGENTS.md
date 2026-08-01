# AGENTS.md

Interactive AI coding agent with a Textual TUI. Streams from an OpenAI-compatible chat API; runs tools in a loop until the model stops.

## Run

- Python >=3.11, managed with `uv`. Dependencies already installed in `.venv` at repo root.
- No `.env` file is used. All config lives in `<data_dir>/config.json` (see below).
- Run from the repo **root**: `.venv/bin/python src/main.py`. Imports are absolute (`from src.*`), so the root must be the working directory. `main.py` adds the repo root to `sys.path`.
- `src/main.py` is the console-script target (`xagent = main:main`); `dist/` is stale build output, ignore it.

## Provider / model config

- Provider, model and API keys are managed at runtime via the TUI (`/provider`, `/model`) and persisted to `<data_dir>/config.json` (`~/.local/share/xagent/config.json`, chmod 600).
- Built-in providers come from `data/models.json` (`api` field = base URL; a fallback URL map lives in `src/utils/providers.py` for known providers missing it, including `anthropic`). Custom providers store name/base_url/api_key + cached `/models` list in `config.json`.
- Provider routing: `Session.provider` (`src/agent/session.py`) picks `AnthropicProvider` when the active provider's base URL contains `anthropic` (`is_anthropic_provider` in `src/utils/providers.py`), otherwise `OpenAIProvider`. Switching invalidates it via `session.reset_provider()`.
- Core: `src/utils/providers.py` (`ProviderStore`, `fetch_models`, `get_store`); `src/utils/config.py` resolves the active provider (`get_config`) and exposes the Exa key (`get_exa_api_key`).

## Config.json keys

- `active_provider` / `active_model` / `providers` (per-provider `api_key`, custom providers also `name`/`base_url`/`models`).
- `exa_api_key` — used by the `web_search` / `web_fetch` tools (`src/tools/*` read it via `get_exa_api_key()`).
- `pillow` is an optional extra (image resize in `src/utils/media.py`); install with `uv pip install pillow` if image support is needed.

## Verify

No tests, lint, or CI configured (`tests/` is empty, no pytest/lint/typecheck config). The only real check is launching the TUI and chatting. To smoke-test code, run `.venv/bin/python -c "import src..."` from the repo root.

## Layout

- `src/agent/` — session persistence (`session.py`), message assembly (`manager.py`), stream/tool loop (`loop.py`), tool-output truncation (`truncate.py`), auto-naming (`naming.py`).
- `src/ai/` — `Provider` ABC, `OpenAIProvider`, `AnthropicProvider`; yields `StreamEvent`s (types in `src/types/events.py`).
- `src/tools/` — tool modules. Each file is auto-registered at runtime if it defines a module-level `tool` (a `Tool` from `src/types/tools.py`). Signature must be `execute(**args)` returning a `ToolResult` or dict; a `to_model_output` function can reformat results.
- `src/ui/tui/` — Textual TUI. Slash commands live in `commands.py` (`/new`, `/session`, `/exit`).
- `src/prompts/*.md` — system prompts loaded by name (`load_prompt`), `default.md` is the main one.

## State

- Sessions persist as JSON under `~/.local/share/xagent/sessions/` (override with `XAGENT_DATA_DIR`); index at `sessions_index.json`.
- Tool output >2000 lines or >50KB is truncated to a file under `/tmp/xagent/truncation/tool_*` (7-day cleanup on session release).
- Keep output verbosity rules in `src/tools/bash.py`'s `Tool.description` and `src/prompts/default.md` consistent — the model's tool policy is defined there, not in code.
