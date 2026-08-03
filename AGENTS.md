# AGENTS.md

xagent — interactive AI coding agent with a Textual TUI. Streams from an
OpenAI-compatible or Anthropic-compatible chat API; runs tools in a loop until
the model stops.

## Run

- Python >=3.11, managed with `uv`. Dependencies already installed in `.venv`
  at repo root.
- No `.env` file is used. All config lives in `<data_dir>/config.json`
  (`~/.local/share/xagent/config.json`, chmod 600; override with
  `XAGENT_DATA_DIR`).
- Run from the repo **root**: `.venv/bin/python src/main.py`. Imports are
  absolute (`from src.*`), so the root must be the working directory.
- `src/main.py` is the console-script target (`xagent = src.main:main`).
  Packaging is configured via `pyproject.toml` (`[tool.setuptools.packages.find]`
  includes `src` and `src.*`; `src/__init__.py` exists to make `src` a regular
  package). `dist/` and `src/*.egg-info/` are ignored build output, not app code.
- `.opencode/` is an ignored node_modules dump, not app code.

## Provider / model config

- Provider, model and API keys are managed at runtime via the TUI
  (`/provider`, `/model`) and persisted to `<data_dir>/config.json`.
- Built-in providers come from `data/models.json` (`api` field = base URL; a
  fallback URL map lives in `src/utils/providers.py` for known providers
  missing it, including `anthropic`). Custom providers store
  name/base_url/api_key + cached `/models` list in `config.json`.
- `data/models.json` is the models.dev catalog (see THIRD_PARTY_NOTICES.md);
  do not commit real API keys anywhere.
- Provider routing: `Session.provider` (`src/agent/session.py`) picks
  `AnthropicProvider` when the active provider's base URL contains
  `anthropic` (`is_anthropic_provider` in `src/utils/providers.py`), otherwise
  `OpenAIProvider`. Switching invalidates it via `session.reset_provider()`.
- Core: `src/utils/providers.py` (`ProviderStore`, `fetch_models`,
  `get_store`); `src/utils/config.py` resolves the active provider
  (`get_config`) and exposes the Exa key (`get_exa_api_key`).

## Config.json keys

- `active_provider` / `active_model` / `providers` (per-provider `api_key`,
  custom providers also `name`/`base_url`/`models`).
- `exa_api_key` — used by the `web` tool (`src/tools/web.py` reads it via
  `get_exa_api_key()`).
- `pillow` is an optional extra (image resize in `src/utils/media.py`);
  install with `uv pip install pillow` if image support is needed.

## Retry semantics (src/agent/turn.py)

- Retryable: 429 / 408 / 409 / 5xx / timeouts / connection errors / retry
  hints (English and Chinese, e.g. "速率限制"). Not retryable: 400 / 401 /
  402 / 403 / 404 / 422 and auth/quota/billing hints.
- Max 3 retries with backoff 5s → 10s → 20s; the countdown is interruptible
  and shown in the TUI. The budget resets after each successful request.
- `provider-error` StreamEvents from the ai layer are raised as
  `_ProviderError` (with `status_code`) inside the turn loop so they go
  through the same retry path.
- Ai-layer streams (`src/ai/*.py`) emit `provider-error` events with a real
  HTTP status code in `data["code"]`; keep it that way when adding providers.

## Tools

- `src/tools/` — tool modules. Each file auto-registers at runtime if it
  defines a module-level `tool` (a `Tool` from `src/types/tools.py`).
  Signature must be `execute(**args)` returning a `ToolResult` or dict; a
  `to_model_output` function can reformat results.
- read/edit semantics mirror opencode (see THIRD_PARTY_NOTICES.md): read
  caps output at 50KB and always emits `N:content`-prefixed lines; edit uses
  a replacer chain with similarity fallbacks, strips BOM before matching, and
  refuses multi-match edits unless `replaceAll`.

## TUI

- `src/ui/tui/` — Textual TUI. Slash commands in `commands.py`: `/new`,
  `/session`, `/provider`, `/model`, `/exa`, `/exit`.
- Keys: Enter sends; Ctrl+C twice within 3s interrupts a running turn;
  Ctrl+D in the session picker deletes a session (confirm with a second
  press).
- Rendering is custom canvas-based (`canvas.py`/`render.py`/`lazy.py`), not
  Textual Markdown widgets; `css.py` styles only the widgets that exist (no
  legacy bubble classes).

## State

- Sessions persist as JSON under `<data_dir>/sessions/`; index at
  `sessions_index.json`.
- Tool output >2000 lines or >50KB is truncated to a file under
  `/tmp/xagent/truncation/tool_*` (7-day cleanup on session release).
- Keep output verbosity rules in `src/tools/bash.py`'s `Tool.description` and
  `src/prompts/default.md` consistent — the model's tool policy lives there.

## Verify

- Smoke tests: `uv run pytest tests/ -q` (or `.venv/bin/python -m pytest`).
  Tests cover read/edit tool edge cases and the retry path; they must not
  touch the network or the real config dir (set `XAGENT_DATA_DIR` to a temp
  dir when a Session is involved).
- Packaging check: `pip install .` into a clean venv, then run `xagent` from
  a directory outside the repo (must not raise `ModuleNotFoundError`).
- No lint or CI is configured.

## Layout

- `src/agent/` — session persistence (`session.py`), message assembly
  (`manager.py`), stream/tool loop (`loop.py`), turn orchestration + retry
  (`turn.py`), tool-output truncation (`truncate.py`), auto-naming
  (`naming.py`).
- `src/ai/` — `Provider` ABC, `OpenAIProvider`, `AnthropicProvider`; yields
  `StreamEvent`s (types in `src/types/events.py`).
- `src/ui/tui/` — Textual TUI: app, commands, pickers/dialogs, canvas
  renderer, markdown highlighter, CSS.
- `src/prompts/*.md` — system prompts loaded by name (`load_prompt`),
  `default.md` is the main one.

## Docs

- README.md (en) / README.zh.md (zh) — keep in sync when user-facing behavior
  changes. THIRD_PARTY_NOTICES.md — third-party licenses; never remove it.
- LICENSE is MIT (Copyright (c) 2026 xp266).
