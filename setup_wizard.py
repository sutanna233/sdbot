"""首次启动配置助手

检查 config.yaml，如果不存在或关键字段为空，引导用户交互式完成初始配置。
"""

import sys
from pathlib import Path

import requests
import yaml

from config_store import ConfigStore


# ── rich helpers ──────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


# ── colour constants (used without rich too) ─────────────────────

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def _warn(msg):
    print(f"  {YELLOW}!{RESET} {msg}")


def _err(msg):
    print(f"  {RED}✗{RESET} {msg}")


def _info(msg):
    print(f"  {CYAN}i{RESET} {msg}")


# ── helper: ask a question ────────────────────────────────────────

def _ask(prompt_text, default=None):
    """Semi-raw input that falls back to built-in input when rich is
    unavailable or the terminal is too simple for rich.prompt."""
    if _HAS_RICH and sys.stdin.isatty():
        return Prompt.ask(prompt_text, default=default) if default else Prompt.ask(prompt_text)
    dflt = f" [{default}]" if default else ""
    try:
        val = input(f"{BOLD}{prompt_text}{dflt}: {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        val = ""
    return val or default or ""


def _confirm(prompt_text, default=True):
    if _HAS_RICH:
        return Confirm.ask(prompt_text, default=default)
    dflt = "Y/n" if default else "y/N"
    val = input(f"{prompt_text} [{dflt}]: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes")


# ── connection testers ────────────────────────────────────────────

def _test_sd_api(base_url):
    url = base_url.rstrip("/") + "/sdapi/v1/options"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            model = data.get("sd_model_checkpoint", data.get("checkpoint", "?"))
            _ok(f"SD WebUI 连接成功，当前模型: {model}")
            return True, model
        _err(f"SD API 返回 HTTP {r.status_code}")
        return False, None
    except requests.ConnectionError:
        _err(f"无法连接 {url}，请确认 SD WebUI 已启动且 --api 已启用")
        return False, None
    except Exception as e:
        _err(f"连接测试失败: {e}")
        return False, None


def _test_llm(provider, base_url, model, api_key):
    """Send a minimal chat completion to verify the LLM endpoint."""
    from openai import OpenAI
    try:
        client = OpenAI(base_url=base_url.rstrip("/") + "/v1", api_key=api_key, timeout=15)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "回复 OK 两个字"}],
            max_tokens=10,
        )
        content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        _ok(f"LLM 测试通过: {content[:50]}")
        return True
    except Exception as e:
        _err(f"LLM 连接失败: {e}")
        return False


def _fetch_models(base_url, api_key):
    """Call /v1/models to discover available models."""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    models_url = f"{url}/models"
    try:
        hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = requests.get(models_url, headers=hdrs, timeout=15)
        if resp.status_code != 200:
            _warn(f"获取模型列表失败 (HTTP {resp.status_code})")
            return []
        data = resp.json()
        raw_models = []
        if isinstance(data, list):
            raw_models = [(m.get("id") or m.get("model")) if isinstance(m, dict) else str(m) for m in data]
        elif isinstance(data, dict) and "data" in data:
            raw_models = [(m.get("id") or m.get("model")) if isinstance(m, dict) else str(m) for m in data["data"]]
        models = list(dict.fromkeys(str(m).strip() for m in raw_models if m))
        if models:
            _ok(f"发现 {len(models)} 个可用模型")
        return models
    except Exception as e:
        _warn(f"无法获取模型列表: {e}")
        return []


# ── config writer ─────────────────────────────────────────────────

EXAMPLE_PATH = Path(__file__).parent / "config.example.yaml"


def _load_example():
    if EXAMPLE_PATH.exists():
        with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def _write_config(data):
    path = Path(__file__).parent / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    _ok(f"配置文件已写入: {path}")
    return path


# ── the wizard itself ─────────────────────────────────────────────

PROVIDER_PRESETS = {
    "1": {
        "label": "DeepSeek",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "2": {
        "label": "OpenAI",
        "provider": "openai",
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini",
    },
    "3": {
        "label": "LM Studio (本地)",
        "provider": "lmstudio",
        "base_url": "http://127.0.0.1:1234",
        "model": "qwen2.5-7b-instruct",
    },
    "4": {
        "label": "SiliconFlow",
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn",
        "model": "deepseek-ai/DeepSeek-V3",
    },
    "5": {
        "label": "自定 (Custom)",
        "provider": "",
        "base_url": "",
        "model": "",
    },
}


def _pick_provider():
    print(f"\n  {BOLD}选择 LLM 服务商{RESET}")
    print(f"  {'─' * 40}")
    for key, preset in PROVIDER_PRESETS.items():
        print(f"  {key}. {preset['label']}")
    while True:
        choice = _ask("请输入编号", default="1")
        preset = PROVIDER_PRESETS.get(choice)
        if preset:
            return dict(preset)
        _err("无效选择，请重新输入")


def run_wizard():
    """Run interactive setup wizard.

    Returns True if config was written, False if user cancelled.
    """
    if _HAS_RICH:
        console = Console()
        console.print()
        console.print(Panel(
            Text("欢迎使用 sdbot — Stable Diffusion 绘图助手", style="bold #D88CFF"),
            subtitle="首次启动配置向导",
            border_style="#D88CFF",
        ))
    else:
        print(f"\n  {BOLD}欢迎使用 sdbot — 首次启动配置向导{RESET}")
        print(f"  {'=' * 50}")

    print()
    _info("本向导将帮你完成基本配置，只需几分钟。")
    _info("可随时按 Ctrl+C 退出，配置不会保存。")

    if not _confirm("\n开始配置", default=True):
        print("  已取消")
        return False

    config = _load_example()

    # ── Step 1: SD WebUI ──────────────────────────────────────
    print(f"\n  {BOLD}【1/4】Stable Diffusion WebUI 地址{RESET}")
    _info("确保 SD WebUI 已启动并带有 --api 参数")
    sd_url = _ask("SD WebUI 地址", default=config.get("sd_api", {}).get("base_url", "http://127.0.0.1:7860"))
    config.setdefault("sd_api", {})["base_url"] = sd_url

    if _confirm("  测试连接", default=True):
        ok, _ = _test_sd_api(sd_url)
        if not ok:
            _warn("连接失败，可稍后在 config.yaml 中修改地址")

    # ── Step 2: LLM ───────────────────────────────────────────
    print(f"\n  {BOLD}【2/4】LLM 语言模型配置{RESET}")
    _info("用于自然语言理解和提示词生成，需兼容 OpenAI API")
    preset = _pick_provider()

    if choice := preset.get("provider"):
        llm_provider = preset["provider"]
        llm_base_url = preset["base_url"]
        llm_model = preset["model"]
    else:
        llm_provider = _ask("provider 名称 (如 deepseek/openai)", default="deepseek")
        llm_base_url = _ask("API 地址", default="https://api.deepseek.com")
        llm_model = _ask("模型名", default="deepseek-chat")

    api_key = _ask("API Key")

    # ── Discover available models via /v1/models ──────────────
    discovered_models = _fetch_models(llm_base_url, api_key)
    selected_models = []

    if discovered_models and _confirm("  发现可用模型，要选择使用哪个吗", default=True):
        print(f"\n  {BOLD}可用模型列表:{RESET}")
        for i, m in enumerate(discovered_models, 1):
            print(f"  {i:2d}. {m}")
        print(f"  {'─' * 40}")
        choice = _ask("请选择编号（可多选，逗号分隔，如 1,3,5）", default="1")
        indices = []
        for part in choice.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(discovered_models):
                    indices.append(idx)
        if indices:
            selected_models = [discovered_models[i] for i in dict.fromkeys(indices)]
        else:
            selected_models = [discovered_models[0]]
    else:
        # Fallback: use preset model or manual input
        selected_models = [llm_model]

    # Use first selected as primary chat model
    primary_model = selected_models[0]
    config["llm"] = {
        "provider": llm_provider,
        "base_url": llm_base_url,
        "model": primary_model,
        "api_key": api_key,
    }

    if _confirm("  测试连接", default=True):
        _test_llm(llm_provider, llm_base_url, primary_model, api_key)

    # Register all selected models
    config.setdefault("models", {})
    selection_chat_set = False
    for model_name in selected_models:
        m_key = f"{llm_provider}_{model_name.replace('/', '_')}"
        config["models"][m_key] = {
            "_key": m_key,
            "provider": llm_provider,
            "base_url": llm_base_url,
            "model": model_name,
            "api_key": api_key,
            "capabilities": ["chat"],
        }
        if not selection_chat_set:
            config.setdefault("selection", {})["chat"] = m_key
            selection_chat_set = True

    # Propagate API key to existing models of the same provider
    for m_key, m_cfg in config.get("models", {}).items():
        if m_cfg.get("provider") == llm_provider and not m_cfg.get("api_key"):
            m_cfg["api_key"] = api_key
            _info(f"已同步 API Key 到 {m_key}")

    # ── Vision model (optional) ──────────────────────────────────
    print(f"\n  {BOLD}【可选】识图模型配置{RESET}")
    _info("如果不使用识图功能（如看图分析），直接 Enter 跳过")
    if _confirm("  需要配置识图模型吗", default=False):
        vision_provider = _ask("  provider 名称", default=llm_provider)
        vision_base = _ask("  API 地址", default=llm_base_url)
        # Try to discover vision-capable models
        vision_models = _fetch_models(vision_base, api_key)
        vision_model_name = ""
        if vision_models:
            print(f"\n  {BOLD}可用模型:{RESET}")
            for i, m in enumerate(vision_models, 1):
                print(f"  {i:2d}. {m}")
            print(f"  {'─' * 40}")
            v_choice = _ask("请选择编号", default="1")
            if v_choice.isdigit():
                idx = int(v_choice) - 1
                if 0 <= idx < len(vision_models):
                    vision_model_name = vision_models[idx]
        if not vision_model_name:
            vision_model_name = _ask("  模型名", default="gpt-4o-mini")
        vision_ak = _ask("  API Key (留空继承主模型 Key)")
        if not vision_ak:
            vision_ak = api_key
        v_key = f"{vision_provider}_{vision_model_name.replace('/', '_')}"
        config.setdefault("models", {})[v_key] = {
            "_key": v_key,
            "provider": vision_provider,
            "base_url": vision_base,
            "model": vision_model_name,
            "api_key": vision_ak,
            "capabilities": ["chat", "vision"],
        }
        config.setdefault("selection", {})["vision"] = v_key
        _ok(f"识图模型: {v_key}")
        if _confirm("  测试识图模型", default=True):
            _test_llm(vision_provider, vision_base, vision_model_name, vision_ak)

    # ── Step 4: Telegram (optional) ───────────────────────────
    print(f"\n  {BOLD}【4/5】Telegram Bot (可选){RESET}")
    _info("如不需要 Telegram 功能，直接按 Enter 跳过")
    tg_token = _ask("Telegram Bot Token (留空跳过)")
    if tg_token:
        config.setdefault("telegram", {})["token"] = tg_token
        config["telegram"]["allowed_users"] = []
        _ok("Telegram 已配置，可在 config.yaml 中设置 allowed_users")

    # ── Step 5: Generation defaults ───────────────────────────
    print(f"\n  {BOLD}【5/5】生成参数默认值{RESET}")
    _info("以下参数可在后续使用中随时调整")
    gen = config.setdefault("generation", {})
    gen["width"] = int(_ask("默认宽度", default=str(gen.get("width", 1024))))
    gen["height"] = int(_ask("默认高度", default=str(gen.get("height", 1536))))
    gen["steps"] = int(_ask("采样步数 (steps)", default=str(gen.get("steps", 28))))
    gen["cfg_scale"] = float(_ask("CFG Scale", default=str(gen.get("cfg_scale", 5))))
    gen["sampler"] = _ask("采样器", default=gen.get("sampler", "Euler"))

    # ── Summary ───────────────────────────────────────────────
    print()
    print(f"  {BOLD}配置摘要{RESET}")
    print(f"  {'─' * 50}")
    print(f"  SD WebUI:   {config['sd_api']['base_url']}")
    print(f"  LLM:        {config['llm']['provider']} / {config['llm']['model']}")
    print(f"  Telegram:   {'已配置' if config.get('telegram', {}).get('token') else '跳过'}")
    print(f"  生成:       {gen['width']}x{gen['height']}  {gen['steps']}steps  CFG {gen['cfg_scale']}  {gen['sampler']}")
    models_count = len(config.get("models", {}))
    print(f"  模型数:     {models_count} 个")
    for m_key, m_cfg in config.get("models", {}).items():
        caps = ",".join(m_cfg.get("capabilities", ["chat"]))
        print(f"               {m_key}  [{caps}]")
    print()

    if _confirm("确认写入配置", default=True):
        _write_config(config)
        print()
        _ok("配置完成！可随时修改 config.yaml 或运行 python sdbot.py config 查看/修改配置")
        return True

    _warn("配置未保存")
    return False


# ── standalone entry (python -m setup_wizard) ─────────────────────

if __name__ == "__main__":
    run_wizard()
