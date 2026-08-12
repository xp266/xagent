<p align="center">
  <a href="./README.md"><img src="https://img.shields.io/badge/English-666666?style=for-the-badge" alt="English"></a>
  <a href="./README.zh.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-0865c2?style=for-the-badge" alt="简体中文"></a>
</p>

<p align="center">
  <img src="./assets/logo.png" width="240" alt="xAgent">
</p>


使用 python 构建的一个简易终端 AI 编程代理(TUI)

项目结构非常简单，通过修改提示词或添加工具可自定义打造 Agent。

TUI 基于 [Textual](https://textual.textualize.io/) 构建
此项目对 TUI 的渲染进行了多轮优化，提高了兼容性并优化了卡顿问题。

模型数据来源 [models.dev](https://models.dev)

## 快速启动

环境：Python >= 3.11(推荐用 [uv](https://docs.astral.sh/uv/) 管理,`uv` 可自动下载 Python),需要 TTY(在终端中运行)

### 方式一:构建 wheel 并全局安装

```bash
uv build
uv tool install --force dist/xagent-*.whl
xagent
```

### 方式二:从源码运行

```bash
# Linux / macOS
./scripts/setup.sh        # 一键:创建 .venv、安装依赖、预拉模型目录

# Windows(cmd)
scripts\setup.bat

uv run xagent
```

> setup 脚本默认直接使用 `uv.lock` 锁定的镜像(tsinghua)。如需指定其他 PyPI 源:
> `XAGENT_PYPI_INDEX=https://pypi.org/simple ./scripts/setup.sh`(Windows 同理设置环境变量)。

## 配置与数据

默认数据目录(可用 `XAGENT_DATA_DIR` 覆盖):

| 平台 | 路径 |
|---|---|
| Linux | `~/.local/share/xagent/` |
| macOS | `~/Library/Application Support/xagent/` |
| Windows | `%LOCALAPPDATA%\xagent\` |

```
xagent/
├── config.json            # 配置文件
├── models_catalog.json    # 模型数据
├── sessions_index.json    # 会话索引
├── AGENTS.md              # 全局 md 文档(默认为空)
└── sessions/              # 会话内容(JSON)
```

项目级配置(`AGENTS.md`)：在启动时会自动加载项目下的 `AGENTS.md`。

MCP 服务器配置示例(`config.json`)：
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
> 除 HTTP(`url` + `headers`)外,同样支持 stdio(`command` + `args` + `env`)。

