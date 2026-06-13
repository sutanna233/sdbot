# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# First-time setup (interactive config wizard)
python sdbot.py setup

# Or: auto-triggered when no config.yaml exists
python sdbot.py shell

# Start WebUI (default port 7861)
python sdbot.py webui --port 7861

# Telegram Bot
python sdbot.py telegram start

# Natural language image generation
python sdbot.py dream "prompt description"

# Batch artist combo generation
python sdbot.py run --mode combo --num 20

# Run tests (core Agent flow tests, no external dependencies required)
python -B -m unittest discover -s tests -v

# Run a single test class
python -B -m unittest tests.test_agent_flow -v
```

## Architecture Overview

### Entry Points

`sdbot.py` — thin entry point that calls `SDArtistTester.main()` in `generate_artists.py`. All CLIs are dispatched by `cli_dispatch.py` parsing args from `cli_args.py`. TUI mode uses `cli_tui.py` (rich-based interactive terminal).

### Core Class: `SDArtistTester` (`generate_artists.py`)

Central orchestrator holding config, model clients, session store, tool registry, agent pipeline, and the generation loop. Instantiated once at startup. Key attributes:

- `self.config` — loaded from `config.yaml` via `ConfigStore`
- `self._chat_model` / `self._vision_model` — lazy-loaded `ModelClient` instances
- `self.session_store` / `self.sessions` — conversation persistence
- `self.agent` — `AgentPipeline` instance
- `self.tool_registry` — `ToolRegistry` built by `build_tool_registry()`
- `self.chain_runner` — executes planned tool chains with callbacks
- `self.character_resolver` — Danbooru alias/tag cache resolution
- `self.danbooru` — `DanbooruTagSearch` for tag lookup
- `self.tag_site` — `TagSite` for structured character prompt tags

### Agent Pipeline (`agent/`)

The pipeline processes user input through stages, defined in `AgentPipeline.process()` (`agent/pipeline.py`):

1. **Artifact reference handler** — quick-route "完整提示词", "输出目录" etc.
2. **`IntentRouter`** (`agent/intent.py`) — classifies input into intent types (new_dream, continue_dream, contextual_followup, command, chat, etc.)
3. **`ContextBuilder`** (`agent/context.py`) — builds conversation history + state for LLM
4. **`LLMPlanner`** (`agent/planner.py`) — calls LLM with tool schemas, gets JSON action plan
5. **`ActionValidator`** (`agent/validator.py`) — validates LLM's action/output structure
6. **`ActionRepair`** (`agent/repair.py`) — fixes common LLM mistakes in action plans
7. **`ConversationState`** (`agent/state.py`) — manages task lifecycle states (researching → research_done → needs_character_confirmation → waiting_choice → executing)
8. **`AgentMemory`** (`agent/memory.py`) — conversation summarization and long-term memory
9. **`SafetyPolicy`** (`agent/safety.py`) — SSRF protection for web fetching, URL validation

### Conversation State Machine (`agent/state.py`)

Tasks flow through these states:
- `researching` → `research_done` | `needs_character_confirmation` | `research_failed`
- `waiting_choice` → `executing` | `cancelled`
- `executing` → `completed` | `failed`

State is persisted per-session in `conversation_state` dict within sessions.

### Tool System (`tools/`)

Tools are registered in `tools/registry.py` (`ToolRegistry`) and built by `tools/bootstrap.py` (`build_tool_registry()`). Each tool has a schema with `allowed_params`, `required`, `description`, `param_hints`, `examples`, and `triggers`. Key tools:

| Tool | File | Purpose |
|------|------|---------|
| `dream` | `tools/dream.py` | Generate images via SD WebUI |
| `character_resolve` | `tools/characters.py` | Resolve character name to Danbooru tags via alias cache |
| `character_confirm` | `tools/characters.py` | User confirmation for ambiguous character matches |
| `tagsite` | `tools/tagsite.py` | Query structured character prompt tags |
| `tags` | `tools/tags.py` | Search Danbooru tags |
| `models` | `tools/models.py` | List/switch LLM providers |
| `web_fetch` | `tools/web_fetch.py` | Fetch web pages (SSRF-safe) |
| `memory_set/get/forget/list` | `tools/memory.py` | Long-term memory CRUD |
| All session tools | `tools/session.py` | Session management |
| File tools | `tools/files.py` | File read/write/list/find/delete |
| Skill tools | `tools/skills.py` | Load/list/create skills |

`ToolExecutor` (`tools/executor.py`) wraps tool execution with stdout capture and structured result formatting (`ok`, `summary`, `output`, `result`, `error`).

### LLM Client (`llm.py`)

`ModelClient` wraps OpenAI-compatible API. Supports chat and vision capabilities. Tracks token usage and cost. Key methods used by Agent pipeline: `chat()`, `agent_chat()` (returns structured JSON), `write_detailed_prompt()`, `analyze_intent()`, `generate_tags()`.

### Session & Memory (`session_store.py`, `agent/state.py`, `agent/memory.py`)

- Sessions stored in `sessions.json` with conversation history and state
- `ConversationState` tracks active task, last choices, last artifact, last tool result
- `AgentMemory` handles automatic conversation summarization every N turns
- Long-term memory stored in session via `memory_set`/`memory_get` tools

### Generation Modes

- `combo` — random 3-8 artist tag combos
- `single` — one artist tag per image
- `pair` — two artist tags per image
- `sequential` — chunked sequential artists
- `weighted` — weighted sampling from `weights.yaml`

### WebUI (`webui.py`)

Flask app with `JobQueue` for async generation. Routes registered via `register_routes()`. Templates in `templates/`, static files in `static/`. REST API under `/api/`.

### Telegram Bot (`telegram_bot.py`)

Runs in a daemon thread with asyncio event loop. Supports inline keyboards for chain confirmation and choice selection. Uses `_execute_chain_sync` for async chain execution with progress updates.

## Tests

`tests/test_agent_flow.py` — core Agent flow tests that do NOT require real LLM, SD WebUI, or Telegram. Tests cover character_resolve → choices flow, choice selection, tagsite structured return, and local alias learning using mocked tool outputs.

`tests/test_linux_compat.py` — Linux compatibility checks.

## Key Configuration (`config.yaml`)

`config.example.yaml` shows all configurable fields. Critical sections:
- `sd_api.base_url` — SD WebUI API endpoint
- `llm` / `models.*` — LLM provider configuration (OpenAI-compatible)
- `selection.chat` — active chat model key
- `telegram` — Telegram Bot settings
- `generation` — default image params (width, height, steps, cfg, sampler)
- `prompt` — prompt templates
- `danbooru` — tag search settings

## Important Files Not to Commit

`config.yaml`, `sessions.json`, `outputs/`, `history.json`, `tag_cache.json`, `lora_triggers.json`, `character_aliases.json` — all in `.gitignore`.
