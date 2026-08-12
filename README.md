<p align="center">
  <a href="./README.md"><img src="https://img.shields.io/badge/English-0865c2?style=for-the-badge" alt="English"></a>
  <a href="./README.zh.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-666666?style=for-the-badge" alt="简体中文"></a>
</p>

<p align="center">
  <img src="./assets/logo.png" width="240" alt="xAgent">
</p>


A simple terminal AI coding agent (TUI) built with Python.

The project structure is very simple — you can customize the agent by modifying the prompts or adding tools.

TUI built on [Textual](https://textual.textualize.io/)
The TUI rendering has been optimized over multiple rounds to improve compatibility and reduce stutter.

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
├── AGENTS.md              # global instructions doc (empty by default)
└── sessions/              # session contents (JSON)
```

Project instructions (`AGENTS.md`): at startup, the `AGENTS.md` under the project is loaded automatically.

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
