# xagent

An interactive AI coding agent with a terminal (Textual) TUI. Streams from
any OpenAI-compatible or Anthropic chat API and runs tools in a loop until the
model stops.

## Features

- **Terminal UI** built with [Textual](https://textual.textualize.io/): chat
  history, streaming output, thinking blocks, tool call display, text
  selection, and folding
- **Tool loop**: the agent can read/edit files, search the codebase
  (`read`, `edit`, `write`, `grep`, `glob`), run shell commands (`bash`),
  and browse the web (`web`, powered by Exa)
- **Provider agnostic**: works with any OpenAI-compatible endpoint
  (DeepSeek, GLM/Zhipu, OpenAI, ...) and Anthropic-compatible endpoints;
  model catalog bundled from [models.dev](https://models.dev/)
- **Session persistence**: chats are saved to disk, resumable anytime
- **Resilient streaming**: transient provider errors (rate limits, timeouts,
  5xx) are retried with exponential backoff and a visible countdown

## Requirements

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An API key for at least one chat provider

## Install

```bash
git clone git@github.com:xp266/xagent.git
cd xagent
uv sync          # or: uv pip install -e .
.venv/bin/python src/main.py
```

You can also install the console script:

```bash
uv pip install -e .
xagent
```

## First run

1. Launch the TUI: `.venv/bin/python src/main.py`
2. Type `/provider` to pick a provider (e.g. `opencode`, `zhipuai`) and enter
   your API key when prompted
3. Type `/model` to select a model
4. Chat!

All configuration lives in `<data_dir>/config.json`
(`~/.local/share/xagent/config.json`, overridable with `XAGENT_DATA_DIR`).
Sessions are stored under `<data_dir>/sessions/`.

## Commands

| Command     | Description                                  |
|-------------|----------------------------------------------|
| `/new`      | Start a new chat                             |
| `/session`  | Switch to a session: `/session <id>`         |
| `/provider` | Switch API provider / set API key            |
| `/model`    | Switch model                                 |
| `/exa`      | Set the Exa API key (web search/fetch)       |
| `/exit`     | Quit xagent                                  |

## Keys

| Key                 | Action                                            |
|---------------------|---------------------------------------------------|
| `Enter`             | Send message                                      |
| `Ctrl+C` ×2 (3s)    | Interrupt the running turn                        |
| `Ctrl+D` (in `/session`) | Delete the selected session (press twice to confirm) |

## How it works

```
src/
├── agent/     session persistence, message assembly, stream/tool loop,
│              retry & truncation, auto-naming
├── ai/        Provider ABC, OpenAIProvider, AnthropicProvider; emits
│              StreamEvent(s)
├── tools/     tool modules; auto-registered if they define a module-level
│              `tool` (a Tool instance). Signature: execute(**args)
├── ui/tui/    Textual TUI (slash commands, pickers, dialogs, custom canvas
│              renderer)
├── prompts/   system prompts (default.md, naming.md)
└── utils/     provider store, config, media helpers
```

## Thanks

- [models.dev](https://models.dev/) — model catalog (MIT, see
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md))
- [opencode](https://github.com/anomalyco/opencode) — design inspiration for
  tool semantics and the agent loop

## License

MIT. See [LICENSE](LICENSE).
