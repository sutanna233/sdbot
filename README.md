# sdbot

sdbot 是一个 Stable Diffusion WebUI API 绘图助手，围绕 artist combo 批量测试、自然语言出图、Agent 工具调用、Telegram Bot 和 WebUI 管理界面构建。

项目适合用来：

- 随机组合 artist tags 批量测试画风。
- 用自然语言生成图片提示词并调用 SD WebUI 出图。
- 通过 CLI/TUI、Telegram 或 WebUI 管理模型、LoRA、历史记录和生成结果。
- 保存跨会话记忆、上一张图参数和最后一次生成信息，支持“完整提示词”“这张”“继续画”等上下文请求。

## 功能

- **Artist Combo**：支持 `combo`、`single`、`pair`、`sequential`、`weighted` 五种采样模式。
- **自然语言出图**：`dream` 命令会分析角色、作品、标签、LoRA 和 artist 组合后生成图片。
- **Agent Shell**：默认启动对话式 CLI，支持工具链、确认执行、记忆、模型管理和上下文续画。
- **Prompt Choices**：绘图请求先给出多种可选构图/强度/场景方向，再按用户选择执行。
- **Telegram Bot**：支持自然语言聊天、Inline Keyboard 选择、执行工具链并回传生成图片。
- **WebUI**：提供浏览器控制台，用于聊天、生成、配置、LoRA、技能和统计查看。
- **生成上下文**：保存 `last_generation`，可查询上一张图的完整 prompt、参数和输出目录。
- **安全配置**：真实 `config.yaml`、会话、缓存和输出目录默认不提交，只发布 `config.example.yaml`。

## 环境要求

- Python 3.10+
- Stable Diffusion WebUI，并启用 API
- 一个 OpenAI-compatible LLM 服务，用于自然语言规划和 prompt 生成
- 可选：Telegram Bot Token

## 安装

```bash
git clone https://github.com/sutanna233/sdbot.git
cd sdbot
pip install -r requirements.txt
```

准备本地配置：

```bash
cp config.example.yaml config.yaml
```

Windows PowerShell 可用：

```powershell
Copy-Item config.example.yaml config.yaml
```

然后编辑 `config.yaml`：

- `sd_api.base_url`：Stable Diffusion WebUI API 地址，例如 `http://127.0.0.1:7860`
- `llm.api_key` / `models.*.api_key`：对话模型密钥
- `selection.chat`：默认对话模型 key
- `telegram.token`：Telegram Bot Token，可留空

`config.yaml` 已在 `.gitignore` 中排除，不要提交真实密钥。

## 快速开始

启动对话式 CLI：

```bash
python sdbot.py shell
```

Windows 也可以直接运行：

```bat
run.bat
```

自然语言出图：

```bash
python sdbot.py dream "画一个白发异色瞳的猫娘"
```

批量测试 artist combo：

```bash
python sdbot.py run --mode combo --num 20
```

启动 WebUI：

```bash
python sdbot.py webui --host 127.0.0.1 --port 7861
```

启动 Telegram Bot：

```bash
python sdbot.py telegram start
```

## 常用命令

| 命令 | 说明 | 示例 |
| --- | --- | --- |
| `shell` | 启动 Agent 对话 CLI | `python sdbot.py shell` |
| `dream` | 自然语言出图 | `python sdbot.py dream "一张星尘头像"` |
| `run` | 批量 artist combo 生成 | `python sdbot.py run --mode single --num 10` |
| `gallery` | 查看或重建画廊 | `python sdbot.py gallery --regenerate` |
| `history` | 查看生成历史 | `python sdbot.py history --last 20` |
| `artists` | 搜索 artist 列表 | `python sdbot.py artists list --search sakura` |
| `loras` | 查看或管理 LoRA | `python sdbot.py loras list` |
| `tags` | 搜索 Danbooru 标签 | `python sdbot.py tags amiya --type character` |
| `tagsite` | 查询角色提示词标签 | `python sdbot.py tagsite "Amiya"` |
| `llm` | 测试或查看 LLM | `python sdbot.py llm status` |
| `config` | 查看或修改配置 | `python sdbot.py config get mode` |
| `webui` | 启动 WebUI | `python sdbot.py webui --port 7861` |
| `telegram` | 管理 Telegram Bot | `python sdbot.py telegram status` |
| `update` | 检查或拉取 GitHub 更新 | `python sdbot.py update --check` |
| `clear` | 清理历史或输出 | `python sdbot.py clear outputs` |

## Agent 工作流

`shell` 模式会启动苏丹娜 Agent。你可以直接输入自然语言，例如：

```text
画一张明日方舟的普瑞赛斯
完整提示词给我一下
这张再来 4 张
现在用的什么模型
列出我的记忆
```

Agent 会根据意图调用工具。通用绘图请求会先给出 prompt choices，选择后再执行 `dream`；包含具体角色、人物或作品名的绘图请求会先用 `tagsite` 查询角色标签，再基于结构化查询结果生成 choices，避免未搜索就脑补角色设定。生成完成后会保存上一张图的结构化信息，因此“完整提示词”“这张”“上一张”“输出目录”等追问可以复用当前会话上下文。

## Telegram Bot

配置 `config.yaml`：

```yaml
telegram:
  token: ""
  allowed_users: []
  auto_start: false
```

启动：

```bash
python sdbot.py telegram start
```

Telegram 支持：

- 普通自然语言聊天和绘图请求。
- 具体角色绘图会先自动查询标签，再发送 Prompt choices inline keyboard。
- 执行生成后回传图片文件。
- 按 Telegram 用户隔离长期记忆命名空间。

如果要限制使用者，把 Telegram user id 填入 `allowed_users`。

## WebUI

启动后访问：

```text
http://127.0.0.1:7861
```

WebUI 包含聊天、生成、批次、配置、LoRA、技能、artist 和统计页面。生成结果默认写入 `outputs/`，画廊文件由 `gallery` 命令或 WebUI 生成。

## 自动更新

本地仓库可以跟随 GitHub 远程仓库更新：

```bash
python sdbot.py update --check
```

发现新提交后执行：

```bash
python sdbot.py update --apply
```

如果更新后还想同步 Python 依赖：

```bash
python sdbot.py update --apply --deps
```

更新功能只使用 `git fetch` 和 `git pull --ff-only`，不会执行 `git reset --hard`，不会自动 stash，也不会覆盖 `config.yaml`、`sessions.json`、`outputs/` 等本地数据。工作区有未提交改动时会拒绝自动更新，避免覆盖你的本地修改。

可在 `config.yaml` 中配置默认远程和分支：

```yaml
update:
  remote: origin
  branch: main
  check_on_start: false
  auto_apply: false
  update_dependencies: false
```

## 配置说明

核心配置来自 `config.yaml`：

```yaml
sd_api:
  base_url: http://127.0.0.1:7860
  auth: null

llm:
  provider: deepseek
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key: ""

models:
  deepseek_deepseek-v4-flash:
    provider: deepseek
    base_url: https://api.deepseek.com
    model: deepseek-v4-flash
    api_key: ""
    capabilities:
      - chat

selection:
  chat: deepseek_deepseek-v4-flash
```

可按需添加更多 OpenAI-compatible 模型。`capabilities` 中包含 `chat` 表示可用于对话，包含 `vision` 表示可用于看图分析。

## 输出与数据

默认生成以下本地数据，均不建议提交：

- `outputs/`：生成图片、prompt txt、`generation_log.json` 和画廊。
- `history.json`：artist combo 去重历史。
- `sessions.json`：Agent 会话、长期记忆、上一张图参数。
- `tag_cache.json`：角色/标签查询缓存。
- `lora_triggers.json`：本地 LoRA 触发词配置。

## 测试

项目包含不依赖真实 LLM、SD WebUI 或 Telegram 的核心 Agent flow 测试：

```bash
python -B -m unittest discover -s tests -v
```

这些测试覆盖角色绘图的 `tagsite -> choices` 流程、choice 选择状态和 `tagsite` 结构化返回。

## 文件结构

```text
sdbot/
├── sdbot.py                 # 推荐启动入口
├── generate_artists.py      # 兼容旧入口和核心生成流程
├── llm.py                   # LLM 客户端和 prompt 生成
├── telegram_bot.py          # Telegram Bot
├── webui.py                 # Flask WebUI
├── agent/                   # Agent 路由、规划、验证、记忆、修复
├── tools/                   # 可被 Agent 调用的工具实现
├── tests/                   # 核心 Agent flow 测试
├── templates/               # WebUI 模板
├── static/                  # WebUI 静态资源
├── artists.txt              # artist 列表
├── config.example.yaml      # 配置模板
└── requirements.txt         # Python 依赖
```

## 安全注意

- 不要提交 `config.yaml`、`sessions.json`、`outputs/` 或任何真实 token。
- 如果 token 曾经发到聊天、日志或公开仓库，请立即撤销并重新生成。
- 输出目录里会复制运行时配置，可能包含密钥，因此 `outputs/` 默认被忽略。
- Telegram Bot 公开运行时建议设置 `allowed_users`。

## License

当前仓库未声明许可证。公开使用或二次分发前请自行补充合适的许可证文件。
