# xagent

一个基于终端（Textual）TUI 的交互式 AI 编码代理。从任意 OpenAI 兼容或
Anthropic 兼容的聊天 API 流式获取回复，并循环调用工具直到模型停止。

## 特性

- **终端界面**：基于 [Textual](https://textual.textualize.io/) 构建，支持
  聊天历史、流式输出、思考块、工具调用展示、文本选择和折叠
- **工具循环**：Agent 可读写文件、搜索代码库（`read`、`edit`、`write`、
  `grep`、`glob`）、执行 Shell 命令（`bash`）、联网搜索（`web`，由 Exa 驱动）
- **Provider 无关**：兼容任意 OpenAI 兼容端点（DeepSeek、GLM/智谱、OpenAI 等）
  与 Anthropic 兼容端点；模型目录内置自 [models.dev](https://models.dev/)
- **会话持久化**：聊天自动保存到磁盘，随时可恢复
- **健壮的流式传输**：瞬时性 Provider 错误（限流、超时、5xx）会自动指数退避
  重试，并显示倒计时

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- 至少一个聊天 Provider 的 API key

## 安装

```bash
git clone git@github.com:xp266/xagent.git
cd xagent
uv sync          # 或：uv pip install -e .
.venv/bin/python src/main.py
```

也可以安装为命令：

```bash
uv pip install -e .
xagent
```

## 首次使用

1. 启动 TUI：`.venv/bin/python src/main.py`
2. 输入 `/provider` 选择 Provider（如 `opencode`、`zhipuai`），按提示输入
   API key
3. 输入 `/model` 选择模型
4. 开始对话！

所有配置存放在 `<data_dir>/config.json`
（`~/.local/share/xagent/config.json`，可用 `XAGENT_DATA_DIR` 覆盖）。
会话存放在 `<data_dir>/sessions/`。

## 命令

| 命令        | 说明                                  |
|-------------|---------------------------------------|
| `/new`      | 开启新对话                           |
| `/session`  | 切换会话：`/session <id>`             |
| `/provider` | 切换 API Provider / 设置 API key     |
| `/model`    | 切换模型                             |
| `/exa`      | 设置 Exa API key（网络搜索/抓取）    |
| `/exit`     | 退出 xagent                          |

## 按键

| 按键                        | 操作                                |
|-----------------------------|-------------------------------------|
| `Enter`                     | 发送消息                            |
| `Ctrl+C` 连续两次（3 秒内） | 中断正在运行的回合                  |
| `Ctrl+D`（在 `/session` 中）| 删除选中的会话（再按一次确认）      |

## 结构

```
src/
├── agent/     会话持久化、消息组装、流式/工具循环、重试与截断、自动命名
├── ai/        Provider 抽象基类、OpenAIProvider、AnthropicProvider；产出
│              StreamEvent 事件
├── tools/     工具模块；定义模块级 `tool`（Tool 实例）即自动注册。
│              签名要求：execute(**args)
├── ui/tui/    Textual TUI（斜杠命令、选择器、弹窗、自定义画布渲染）
├── prompts/   系统提示词（default.md、naming.md）
└── utils/     Provider 存储、配置、媒体辅助
```

## 致谢

- [models.dev](https://models.dev/) — 模型目录（MIT，见
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）
- [opencode](https://github.com/anomalyco/opencode) — 工具语义与 Agent
  循环的设计灵感

## 许可证

MIT，见 [LICENSE](LICENSE)。