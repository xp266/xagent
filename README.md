<p align="center">
  <img src="./assets/logo.png" alt="xAgent">
</p>

A simple terminal AI coding agent (TUI) built with Python.
The project structure is very simple — you can customize the agent by modifying the prompts or adding tools.

TUI built on [Textual](https://textual.textualize.io/)
Model data from [models.dev](https://models.dev)

## Quick start

Requirements: Python >= 3.11 (managing it with [uv](https://docs.astral.sh/uv/) is recommended — uv can install Python itself), needs a TTY (run inside a terminal)

### Option 1: build a wheel and install globally

```bash
uv build
uv tool install --force dist/xagent-*.whl
xagent
```

### Option 2: run from source

```bash
# Linux / macOS
./scripts/setup.sh        # one step: create .venv, install deps, prefetch the model catalog

# Windows (cmd)
scripts\setup.bat

uv run xagent
```

> The setup scripts use the index pinned in `uv.lock` (tsinghua) by default. To use a different PyPI source:
> `XAGENT_PYPI_INDEX=https://pypi.org/simple ./scripts/setup.sh` (set the same env var on Windows).

## Configuration & data

Default data directory (override with `XAGENT_DATA_DIR`):

| Platform | Path |
|---|---|
| Linux | `~/.local/share/xagent/` |
| macOS | `~/Library/Application Support/xagent/` |
| Windows | `%LOCALAPPDATA%\xagent\` |

```
xagent/
├── config.json            # config file
├── models_catalog.json    # model data
├── sessions_index.json    # session index
└── sessions/              # session contents (JSON)
```

MCP server configuration example (`config.json`):
```
  "mcp_servers": {
    "github": {
      "status": "enabled",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {
        "Authorization": "YOU_API_KEY"
      }
    }
  }
```
> Besides HTTP (`url` + `headers`), stdio (`command` + `args` + `env`) is also supported.
