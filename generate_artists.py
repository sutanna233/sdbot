import os, sys, re, time, json, shutil, subprocess, threading
from datetime import datetime
from pathlib import Path

import requests, yaml
from PIL import Image

from llm import ModelClient
from danbooru import DanbooruTagSearch
from telegram_bot import TelegramBot
from tag_site import TagSite
from web import WebFetcher
from config_store import ConfigStore
from generation_helpers import (
    ArtistSampler, build_prompt, combo_fingerprint, create_output_dir, filter_similar,
    get_base_name, is_duplicate, load_artists, mark_generated, sanitize_filename,
    save_image, save_info_txt,
)
from session_store import SessionStore
from agent.host import AgentHost
from agent import AgentPipeline
from agent.chain_runner import ChainRunner
from agent.safety import SafetyPolicy
from tools import ToolExecutor, build_tool_registry
from cli_args import parse_args
from cli_dispatch import dispatch
from cli_tui import TUIController



class SDArtistTester:
    def __init__(self, config_path="config.yaml", cli_args=None):
        self.script_dir = Path(__file__).parent
        self.config_path = self.script_dir / config_path
        self.config_store = ConfigStore(self.config_path)
        self.config = self.config_store.data
        self._init_models_config()

        base_url = self.config["sd_api"]["base_url"].rstrip("/")
        self.api_url = f"{base_url}/sdapi/v1/txt2img"
        self.auth = self.config["sd_api"].get("auth")
        self.session = requests.Session()
        if self.auth:
            self.session.auth = tuple(self.auth.split(":"))

        self.cli_args = cli_args or {}
        dc = self.config.get("dedup", {})
        self.dedup_enabled = dc.get("enabled", True)
        self.similarity_filter = dc.get("similarity_filter", "strict")
        self.allow_resample = dc.get("allow_resample", False)
        self.history_path = self.script_dir / dc.get("history_file", "history.json")
        self.artist_sampler = ArtistSampler(self)
        self.history = self._load_history()
        self.retry = self.config["testing"].get("retry", 2)
        self.continue_on_error = self.config["testing"].get("continue_on_error", True)
        self.mode = self.cli_args.get("mode") or self.config.get("mode", "combo")
        mc = self.config.get("mode_config", {}).get(self.mode, {})
        self.mode_min = mc.get("min_artists") or self.config["testing"].get("min_artists", 3)
        self.mode_max = mc.get("max_artists") or self.config["testing"].get("max_artists", 8)

        self._chat_model = None
        self._vision_model = None
        self._failed_model_keys = set()
        self.danbooru = DanbooruTagSearch(self.config)
        self.sessions_path = self.script_dir / "sessions.json"
        self._sessions_lock = threading.RLock()
        self.session_store = SessionStore(self.sessions_path, self._sessions_lock)
        self.sessions = self.session_store.data
        self._loras_cache = None
        self._loras = []
        self.lora_triggers_path = self.script_dir / "lora_triggers.json"
        self.lora_triggers = self._load_lora_triggers()
        self.tag_site = TagSite(self.script_dir)
        from tools.characters import CharacterResolver
        self.character_resolver = CharacterResolver(self)
        self.web = WebFetcher(self.config)
        self.last_run_dir = None
        self._telegram_bot = None
        self._telegram_running = False
        self._pending_skill_chain = []
        self._pending_skill_body = ""
        self._in_react_loop = False
        self.tui = None
        self.safety = SafetyPolicy()
        self.agent_host = AgentHost(self)
        self.agent = AgentPipeline(self.agent_host)
        self.tool_registry = build_tool_registry(self)
        self.tool_executor = ToolExecutor(self, self.tool_registry)
        self.chain_runner = ChainRunner(self)

    def _init_models_config(self):
        """兼容旧格式: 没有 models: 段时自动从 llm: + vision: 构建."""
        if "models" in self.config:
            self._repair_model_selection(save=False)
            return
        models = {}
        sel = {}
        if "llm" in self.config:
            c = self.config["llm"]
            key = f"{c.get('provider','unknown')}_{c.get('model','unknown')}"
            models[key] = {**c, "capabilities": ["chat"], "_key": key}
            sel["chat"] = key
        if "vision" in self.config:
            c = self.config["vision"]
            key = f"{c.get('provider','unknown')}_{c.get('model','unknown')}"
            models[key] = {**c, "capabilities": ["chat", "vision"], "_key": key}
            sel["vision"] = key
        self.config["models"] = models
        self.config["selection"] = sel
        self._repair_model_selection(save=False)

    def _get_models_list(self):
        return self.config.get("models", {})

    def _get_selection(self):
        return self.config.get("selection", {})

    def _get_selection_provider(self, role):
        """返回 selection.{role} 对应的 provider dict, 含 _key."""
        sel = self._get_selection()
        key = sel.get(role)
        if not key:
            return None
        models = self._get_models_list()
        return models.get(key)

    def _repair_model_selection(self, save=True):
        """Repair stale selection keys after config/provider changes."""
        models = self._get_models_list()
        sel = dict(self._get_selection())
        changed = False
        for role in ("chat", "vision"):
            key = sel.get(role)
            if key in models and role in models[key].get("capabilities", []) and not self._is_known_bad_model(models[key]):
                continue
            replacement = None
            for model_key, cfg in models.items():
                if role in cfg.get("capabilities", []) and not self._is_known_bad_model(cfg):
                    replacement = model_key
                    break
            if replacement:
                sel[role] = replacement
            else:
                sel.pop(role, None)
            changed = True
        if changed:
            self.config["selection"] = sel
            if save:
                self._save_config()
        return changed

    def _selection_model_name(self, role):
        p = self._get_selection_provider(role) or {}
        name = p.get("model") or p.get("_key") or "未配置"
        if p and self._is_known_bad_model(p):
            return f"{name} [ERR]"
        return name

    def _is_known_bad_model(self, cfg):
        err = str((cfg or {}).get("last_error", "")).lower()
        return (cfg or {}).get("status") == "error" and (
            "not found" in err or "404" in err or "model_not_found" in err
        )

    def _mark_model_status(self, key, ok, error=""):
        models = self._get_models_list()
        cfg = models.get(key)
        if not cfg:
            return
        cfg["status"] = "ok" if ok else "error"
        cfg["last_error"] = "" if ok else str(error)[:500]
        self.config["models"] = models
        if ok:
            self._failed_model_keys.discard(key)
        else:
            self._failed_model_keys.add(key)
        self._save_config()

    def _should_persist_model_error(self, error, kind=None):
        kind = kind or self._model_error_kind(error)
        return kind == "not_found"

    def _model_error_text(self, error):
        return str(error or "")

    def _is_not_found_error(self, error):
        return self._model_error_kind(error) == "not_found"

    def _model_error_kind(self, error):
        text = self._model_error_text(error).lower()
        if "返回空 completion" in text or "empty completion" in text:
            return "empty_content"
        if "not found" in text or "404" in text or "not_found_error" in text:
            return "not_found"
        if "content filter" in text or "new_sensitive" in text:
            return "filter"
        if "timed out" in text or "timeout" in text or "connection" in text or "connect" in text:
            return "transport"
        return "unknown"

    def _llm(self):
        """返回当前 chat 模型 ModelClient 实例, 自动 fallback."""
        p = self._get_selection_provider("chat")
        if not p:
            raise RuntimeError("未配置 chat 模型 (config.yaml selection.chat)")
        key = p.get("_key")
        if self._is_known_bad_model(p):
            self._chat_model = self._fallback_model_client("chat", {key}, reason=p.get("last_error", "known bad"))
        elif self._chat_model and self._chat_model._is_failing():
            if self._should_persist_model_error(self._chat_model.last_error, getattr(self._chat_model, "last_error_kind", "")):
                self._mark_model_status(key, False, self._chat_model.last_error or "model client failed")
            else:
                self._failed_model_keys.add(key)
            self._chat_model = self._fallback_model_client("chat", {key}, reason=self._chat_model.last_error)
        elif self._chat_model is None or self._chat_model.key != key:
            self._chat_model = ModelClient(p)
        return self._chat_model

    def _get_vc(self):
        """返回当前 vision 模型 ModelClient 实例, 自动 fallback."""
        p = self._get_selection_provider("vision")
        if not p:
            raise RuntimeError("未配置 vision 模型 (config.yaml selection.vision)")
        key = p.get("_key")
        if self._vision_model and self._vision_model._is_failing():
            fb = self._find_fallback("vision", key)
            if fb:
                print(f"  [FALLBACK] {key} 不可用, 切换到 {fb['_key']}")
                self._switch_selection("vision", fb["_key"])
                self._vision_model = ModelClient(fb)
            else:
                self._vision_model = ModelClient(p)
        elif self._vision_model is None or self._vision_model.key != key:
            self._vision_model = ModelClient(p)
        return self._vision_model

    def _find_fallback(self, role, exclude_keys=None):
        """找到拥有 role 能力且未失败的第一个模型."""
        if isinstance(exclude_keys, str):
            exclude_keys = {exclude_keys}
        excluded = set(exclude_keys or set()) | set(self._failed_model_keys)
        for k, m in self._get_models_list().items():
            if k in excluded:
                continue
            if role not in m.get("capabilities", []):
                continue
            if self._is_known_bad_model(m):
                continue
            return {**m, "_key": k}
        return None

    def _fallback_model_client(self, role, exclude_keys=None, reason=""):
        if isinstance(exclude_keys, str):
            exclude_keys = {exclude_keys}
        excluded = set(exclude_keys or set()) | set(self._failed_model_keys)
        last_error = reason or "current model unavailable"
        while True:
            fb = self._find_fallback(role, excluded)
            if not fb:
                role_name = "chat" if role == "chat" else role
                raise RuntimeError(
                    f"当前没有可用的 {role_name} 模型。"
                    f"请用 /models list 查看模型，/models switch {role_name} <key> 切换。"
                    f"最后错误: {last_error}"
                )
            fb_key = fb["_key"]
            print(f"  [FALLBACK] 尝试切换到 {fb_key}")
            test = ModelClient(fb).test_chat() if role == "chat" else {"ok": True, "error": ""}
            if test.get("ok"):
                self._mark_model_status(fb_key, True)
                self._switch_selection(role, fb_key)
                print(f"  [OK] 已切换到可用模型: {fb_key}")
                return ModelClient(fb)
            last_error = test.get("error", "test failed")
            if self._should_persist_model_error(last_error, test.get("error_kind")):
                self._mark_model_status(fb_key, False, last_error)
            else:
                self._failed_model_keys.add(fb_key)
            excluded.add(fb_key)
            print(f"  [WARN] {fb_key} 不可用: {last_error[:160]}")

    def _switch_selection(self, role, new_key):
        sel = self._get_selection()
        sel[role] = new_key
        self.config["selection"] = sel
        self._save_config()

    def _should_use_context(self, user_input):
        return self.agent.router.should_use_context(user_input)

    def _classify_agent_request(self, user_input):
        return self.agent.router.route(user_input).name

    def _extract_requested_num(self, user_input):
        return self.agent.repair.extract_requested_num(user_input)

    def _get_last_dream_params(self, session):
        return self.agent.context.get_last_dream_params(session)

    def _is_quantity_only_continue(self, user_input):
        return self.agent.repair.is_quantity_only_continue(user_input)

    def _build_agent_input(self, kind, user_input, session):
        if kind not in ("continue_dream", "edit_dream"):
            return user_input
        last = self._get_last_dream_params(session)
        if not last:
            return user_input
        return (
            "[结构化续画上下文]\n"
            f"last_dream_params={json.dumps(last, ensure_ascii=False)}\n"
            "规则：基于 last_dream_params 理解用户请求。"
            "如果用户只是要求再画/继续并指定数量，只能修改 num，必须保留 description、mode、steps、cfg_scale、sampler 等参数。"
            "如果用户要求加内容或修改内容，必须保留上一张的核心主体，再追加或替换用户明确提出的部分。"
            "不要引入聊天历史中未出现在 last_dream_params 或当前请求里的主题。\n"
            f"[用户请求]\n{user_input}"
        )

    def _clean_agent_reply(self, text):
        return self.agent.repair.clean_reply(text)

    def _save_last_dream_params_from_result(self, result, session):
        return self.agent.memory.save_last_dream_params_from_result(result, session)

    def _guard_agent_result(self, kind, user_input, result, session):
        from agent.types import Intent, AgentContext
        ctx = AgentContext("legacy", [], {"last_dream_params": self._get_last_dream_params(session)})
        return self.agent.repair.repair(Intent(kind), user_input, result, ctx)

    def _test_model_key(self, model_key, save=True):
        models = self._get_models_list()
        cfg = models.get(model_key)
        if not cfg:
            return {"ok": False, "error": f"未知模型: {model_key}", "text": ""}
        if "chat" not in cfg.get("capabilities", []):
            return {"ok": False, "error": f"{model_key} 不支持 chat 测试", "text": ""}
        client = ModelClient({**cfg, "_key": model_key})
        result = client.test_chat()
        if save:
            if result.get("ok"):
                cfg["status"] = "ok"
                cfg["last_error"] = ""
                self.config["models"] = models
                self._save_config()
            elif self._should_persist_model_error(result.get("error", ""), result.get("error_kind")):
                cfg["status"] = "error"
                cfg["last_error"] = result.get("error", "")
                self.config["models"] = models
                self._save_config()
        return result

    def cmd_models(self, action="list", role=None, model_key=None):
        if action == "status":
            sel = self._get_selection()
            models = self._get_models_list()
            chat_key = sel.get("chat")
            vision_key = sel.get("vision")
            chat = models.get(chat_key, {})
            vision = models.get(vision_key, {})
            print(f"  当前对话模型: {chat.get('model', chat_key or '未配置')} ({chat_key or '-'})")
            print(f"  当前识图模型: {vision.get('model', vision_key or '未配置')} ({vision_key or '-'})")
        elif action == "list":
            models = self._get_models_list()
            sel = self._get_selection()
            if not models:
                print("  没有配置任何模型")
                return
            print(f"\n  {'─' * 55}")
            for key, m in models.items():
                caps = ",".join(m.get("capabilities", ["chat"]))
                status = m.get("status")
                tag = ""
                if sel.get("chat") == key:
                    tag += " [对话]"
                if sel.get("vision") == key:
                    tag += " [识图]"
                if status == "ok":
                    tag += " [OK]"
                elif status == "error":
                    tag += " [ERR]"
                if self._is_known_bad_model(m):
                    tag += " [不可用]"
                print(f"  {key}{tag}")
                print(f"    provider={m.get('provider')}  model={m.get('model')}")
                if status == "error" and m.get("last_error"):
                    print(f"    last_error={m.get('last_error')[:160]}")
            print(f"  {'─' * 55}")
        elif action == "switch":
            if not role or not model_key:
                print("  用法: models switch chat|vision <model_key>")
                return
            models = self._get_models_list()
            if model_key not in models:
                print(f"  未知模型: {model_key}  可用: {', '.join(models.keys())}")
                return
            caps = models[model_key].get("capabilities", [])
            if role not in caps:
                print(f"  [ERR] {model_key} 不包含 {role} 能力")
                return
            if role == "chat":
                result = self._test_model_key(model_key)
                if not result.get("ok"):
                    print(f"  [ERR] 模型测试失败，未切换: {result.get('error')}")
                    return
            sel = self._get_selection()
            sel[role] = model_key
            self.config["selection"] = sel
            self._save_config()
            # 重置对应客户端
            if role == "chat":
                self._chat_model = None
            elif role == "vision":
                self._vision_model = None
            m = models[model_key]
            print(f"  [OK] {role} 模型已切换至: {m.get('provider')} / {m.get('model')}")
        elif action == "test":
            models = self._get_models_list()
            keys = [model_key] if model_key else list(models.keys())
            for key in keys:
                if key not in models:
                    print(f"  [ERR] 未知模型: {key}")
                    continue
                if "chat" not in models[key].get("capabilities", []):
                    print(f"  [SKIP] {key} 不支持 chat")
                    continue
                result = self._test_model_key(key)
                if result.get("ok"):
                    self._failed_model_keys.discard(key)
                    print(f"  [OK] {key}: {result.get('text')}")
                else:
                    if result.get("error_kind") in ("not_found", "transport"):
                        self._failed_model_keys.add(key)
                    print(f"  [ERR] {key}: {result.get('error')}")

    def _model_key(self, provider, model):
        raw = f"{provider}_{model}"
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_")

    def cmd_add_provider(self, base_url="", api_key="", provider_name=None, capabilities=None, switch_role=None):
        """测试并注册新 provider 的模型."""
        if not base_url:
            print("  [ERR] 缺少 base_url")
            return
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        models_url = f"{url}/models"
        try:
            hdrs = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = requests.get(models_url, headers=hdrs, timeout=15)
            if resp.status_code != 200:
                print(f"  [ERR] 连接失败 (HTTP {resp.status_code}): {resp.text[:200]}")
                return
            data = resp.json()
        except Exception as e:
            print(f"  [ERR] 请求失败: {e}")
            return

        # Parse /v1/models response (OpenAI-compatible format)
        raw_models = []
        if isinstance(data, list):
            raw_models = [(m.get("id") or m.get("model")) if isinstance(m, dict) else str(m) for m in data]
        elif isinstance(data, dict):
            if "data" in data:
                raw_models = [(m.get("id") or m.get("model")) if isinstance(m, dict) else str(m) for m in data["data"]]
            else:
                raw_models = [data.get("model", "default")]
        raw_models = list(dict.fromkeys(str(m).strip() for m in raw_models if m))

        if not raw_models:
            print(f"  [ERR] 未从 {models_url} 发现模型")
            return

        if provider_name:
            provider = provider_name
        else:
            host = re.sub(r"^https?://", "", url).split("/")[0]
            provider = host.split(".")[0].split(":")[0]
        provider = re.sub(r"[^A-Za-z0-9_.-]+", "_", provider).strip("_") or "provider"
        if isinstance(capabilities, str):
            caps = [c.strip() for c in re.split(r"[,/| ]+", capabilities) if c.strip()]
        else:
            caps = capabilities or ["chat"]
        caps = [c for c in caps if c in ("chat", "vision")]
        if not caps:
            caps = ["chat"]
        models_cfg = self._get_models_list()
        added = 0
        added_keys = []
        for mname in raw_models:
            key = self._model_key(provider, mname)
            if key in models_cfg:
                print(f"  已存在: {key}")
                continue
            models_cfg[key] = {
                "_key": key,
                "provider": provider,
                "model": mname,
                "base_url": url,
                "api_key": api_key,
                "capabilities": list(caps),
            }
            added += 1
            added_keys.append(key)

        if added:
            self.config["models"] = models_cfg
            self._save_config()
            test_results = {}
            for key in added_keys:
                if "chat" not in models_cfg[key].get("capabilities", []):
                    continue
                result = self._test_model_key(key)
                test_results[key] = result
                if result.get("ok"):
                    print(f"  [TEST OK] {key}: {result.get('text')}")
                else:
                    print(f"  [TEST ERR] {key}: {result.get('error')}")
            if switch_role in ("chat", "vision") and switch_role not in caps:
                print(f"  [WARN] 新模型不包含 {switch_role} 能力，未切换当前模型")
            elif switch_role in ("chat", "vision") and added_keys:
                role = switch_role
                if role == "chat":
                    key = next((k for k in added_keys if test_results.get(k, {}).get("ok")), None)
                    if not key:
                        print("  [WARN] 新模型测试均失败，未切换当前模型")
                else:
                    key = added_keys[0]
                if key:
                    sel = self._get_selection()
                    sel[role] = key
                    self.config["selection"] = sel
                    self._save_config()
                    print(f"  [OK] 已自动设为 {role} 模型: {key}")
                    if role == "chat":
                        self._chat_model = None
                    else:
                        self._vision_model = None

        verb = "已添加" if added else "无新增"
        print(f"  [OK] {verb} {added} 个模型 (provider={provider})")
        for mname in raw_models:
            key = self._model_key(provider, mname)
            if key in models_cfg:
                print(f"    {key}")

    # ── config / history ────────────────────────────────────────────

    def _load_config(self):
        self.config_store.data = self.config_store.load()
        self.config = self.config_store.data
        return self.config

    def _save_config(self):
        self.config_store.save(self.config)

    def _load_history(self):
        if not self.dedup_enabled or not self.history_path.exists():
            return {}
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_history(self):
        if not self.dedup_enabled:
            return
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    # ── sessions ────────────────────────────────────────────────────

    def _load_sessions(self):
        self.session_store.data = self.session_store.load()
        self.sessions = self.session_store.data
        return self.sessions

    def _save_sessions(self, data=None):
        if data is not None:
            self.session_store.data = data
            self.sessions = data
        self.session_store.save()

    def _session_create(self, name=None):
        return self.session_store.create(name)

    def _session_delete(self, sid):
        self.session_store.delete(sid)

    def _session_switch(self, target):
        return self.session_store.switch(target)

    def _session_rename(self, sid, name):
        self.session_store.rename(sid, name)

    def _session_list_text(self):
        return self.session_store.list_text()

    def _session_current(self):
        return self.session_store.current()

    # ── dedup helpers ────────────────────────────────────────────────

    def _combo_fingerprint(self, artists):
        return combo_fingerprint(artists)

    def _is_duplicate(self, artists):
        return is_duplicate(self.history, artists)

    def _mark_generated(self, artists, output_path, prompt):
        mark_generated(self.history, artists, output_path, prompt)
        self._save_history()

    def _get_base_name(self, artist):
        return get_base_name(artist)

    def _filter_similar(self, candidates, pool):
        return filter_similar(candidates, self.similarity_filter)

    # ── file helpers ─────────────────────────────────────────────────

    def _sanitize_filename(self, name):
        return sanitize_filename(name)

    def _create_output_dir(self):
        return create_output_dir(self.script_dir, self.config, self.mode)

    def _save_image(self, img_data, path):
        save_image(img_data, path)

    def _save_info_txt(self, artists, prompt, neg, path, seed):
        save_info_txt(self.config, artists, prompt, neg, path, seed)

    def _build_prompt(self, artists):
        return build_prompt(self.config, artists)

    def _load_artists(self):
        return load_artists(self.script_dir, self.config)

    # ── selection strategies ─────────────────────────────────────────

    def _select_artists(self, artists_list, num_images):
        return self.artist_sampler.select(artists_list, num_images)

    def _select_combo(self, a, n):
        return self.artist_sampler.select_combo(a, n)

    def _select_single(self, a, n):
        return self.artist_sampler.select_single(a, n)

    def _select_pair(self, a, n):
        return self.artist_sampler.select_pair(a, n)

    def _select_sequential(self, a, n):
        return self.artist_sampler.select_sequential(a, n)

    def _select_weighted(self, a, n):
        return self.artist_sampler.select_weighted(a, n)

    # ── image generation ─────────────────────────────────────────────

    def _resolve_loras(self, loras, context=""):
        if not loras:
            return loras
        installed = self._fetch_loras()
        if not installed:
            return loras
        actual_names = [l["name"] for l in installed]
        resolved = []
        for l in loras:
            name = l.get("name", l) if isinstance(l, dict) else l
            weight = l.get("weight", 0.8) if isinstance(l, dict) else 0.8
            resolved_name = name if name in actual_names else self._llm_select_lora(name, actual_names, context)
            if not resolved_name:
                print(f"  [WARN] 忽略未知 LoRA: {name}")
                continue
            entry = {"name": resolved_name, "weight": weight, "_original": name}
            if resolved_name != name:
                print(f"  [LoRA] '{name}' -> '{resolved_name}'")
                entry["_display"] = f"{name} -> {resolved_name}"
            else:
                entry["_display"] = resolved_name
            resolved.append(entry)
        self._ensure_lora_triggers(resolved)
        return resolved

    def _llm_select_lora(self, requested, actual_names, context=""):
        system = (
            "你是 LoRA 选择器。根据用户请求和已安装 LoRA 名称，选择唯一最匹配的 LoRA。"
            "只能从 installed_loras 中原样选择 name；如果请求不是 LoRA 名称、没有明确对应项、"
            "或只是 skill/工具/普通概念名称，返回 {\"name\": null}。"
            "不要猜测、不要发明、不要输出解释。只输出 JSON。"
        )
        data = {
            "requested_lora": requested,
            "context": context,
            "installed_loras": actual_names,
        }
        if not actual_names:
            return None
        if len(actual_names) == 1 and str(requested).strip() != actual_names[0]:
            return None
        try:
            text = self._llm()._chat_completion(
                [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                temp=0.1,
                max_tokens=256,
                mark_failure=False,
            )
            parsed = self._llm()._parse_json(text, default={})
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        selected = parsed.get("name")
        return selected if selected in actual_names else None

    def _append_loras(self, prompt, loras):
        if not loras:
            return prompt
        triggers = []
        lora_tokens = []
        for l in loras:
            name = l.get("name", l) if isinstance(l, dict) else l
            weight = l.get("weight", 0.8) if isinstance(l, dict) else 0.8
            trigger = self.lora_triggers.get(name, "")
            if trigger:
                triggers.append(trigger)
            lora_tokens.append(f"<lora:{name}:{weight}>")
        # 触发词放最前 (高权重位置), 让 SD 优先识别 LoRA 角色
        # lora token 放末尾 (技术性参数, 放后面不影响语义)
        if triggers:
            return ", ".join(triggers) + ", " + prompt + ", " + ", ".join(lora_tokens)
        return prompt + ", " + ", ".join(lora_tokens)

    def _load_lora_triggers(self):
        if self.lora_triggers_path.exists():
            with open(self.lora_triggers_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_lora_triggers(self):
        with open(self.lora_triggers_path, "w", encoding="utf-8") as f:
            json.dump(self.lora_triggers, f, ensure_ascii=False, indent=2)

    def _ensure_lora_triggers(self, loras, interactive=True):
        if not loras:
            return
        missing = []
        for l in loras:
            name = l.get("name", l) if isinstance(l, dict) else l
            if name not in self.lora_triggers:
                missing.append(name)
        if not missing:
            return
        if not interactive:
            raise RuntimeError(f"LoRA 缺少触发词: {', '.join(missing)}")
        for l in loras:
            name = l.get("name", l) if isinstance(l, dict) else l
            if name not in self.lora_triggers:
                print(f"\n  [LoRA] '{name}' 未设置触发词，已跳过触发词，仅使用 LoRA token")

    def generate_image(self, artists, output_path, index, total, loras=None):
        prompt = self._append_loras(self._build_prompt(artists), loras)
        c = self.config["generation"]
        payload = {"prompt": prompt, "negative_prompt": self.config["prompt"]["negative"],
                    "width": c["width"], "height": c["height"], "steps": c["steps"],
                    "cfg_scale": c["cfg_scale"], "seed": c["seed"],
                    "sampler_name": c.get("sampler", "Euler a")}
        for attempt in range(1, self.retry + 2):
            try:
                print(f"  [{index}/{total}] A{attempt}: {prompt[:55]}...", flush=True)
                resp = self.session.post(self.api_url, json=payload, timeout=300)
                if resp.status_code != 200:
                    if attempt <= self.retry:
                        continue
                    return False, f"HTTP {resp.status_code}", prompt
                r = resp.json()
                if "images" not in r or not r["images"]:
                    if attempt <= self.retry:
                        continue
                    return False, "No image", prompt
                self._save_image(r["images"][0], output_path)
                seed = r.get("parameters", {}).get("seed") or r.get("seed", "N/A")
                self._save_info_txt(artists, prompt, self.config["prompt"]["negative"], output_path, seed)
                if self.dedup_enabled:
                    self._mark_generated(artists, output_path, prompt)
                print("    OK")
                return True, str(output_path), prompt
            except requests.exceptions.Timeout:
                if attempt <= self.retry:
                    continue
                return False, "Timeout", prompt
            except requests.exceptions.ConnectionError:
                if attempt <= self.retry:
                    continue
                return False, "Connection", prompt
            except Exception as e:
                if attempt <= self.retry:
                    continue
                return False, str(e), prompt

    # ── run ──────────────────────────────────────────────────────────

    def run(self, loras=None):
        self.last_run_dir = None
        artists = self._load_artists()
        num = self.cli_args.get("num") or self.config["testing"].get("num_images", 10)
        if self.mode_min > len(artists):
            print(f"Error: min_artists > available ({len(artists)})")
            sys.exit(1)
        out = self._create_output_dir()
        shutil.copy(self.config_path, out / "config.yaml")
        print(f"\n{'=' * 55}\n  sdbot  |  API: {self.api_url}  Mode: {self.mode}"
              f"  Dedup: {'on' if self.dedup_enabled else 'off'}\n"
              f"  Artists: {len(artists)}  Images: {num}"
              + (f"  Per image: {self.mode_min}-{self.mode_max}" if self.mode in ("combo","weighted") else "")
              + f"\n  Output: {out}\n{'=' * 55}\n")
        selections = self._select_artists(artists, num)
        results = []
        start = time.time()
        skipped = 0
        for i, sel in enumerate(selections, 1):
            try:
                if not sel:
                    skipped += 1
                    continue
                fn = f"{self.mode}_{i:03d}_{'_'.join(self._sanitize_filename(a) for a in sel)[:100]}.png"
                op = out / fn
                ok, info, prompt = self.generate_image(sel, op, i, len(selections), loras)
                results.append({"index": i, "artists": sel, "prompt": prompt, "success": ok, "info": info})
            except Exception as e:
                print(f"    Error: {e}")
                if not self.continue_on_error:
                    raise
                results.append({"index": i, "artists": [], "prompt": "", "success": False, "info": str(e)})
        elapsed = time.time() - start
        ok_count = sum(1 for r in results if r["success"])
        print(f"\n{'=' * 55}\n  Done in {elapsed:.1f}s  Success: {ok_count}/{len(selections)}"
              + (f"  Skipped: {skipped}" if skipped else "") + f"\n{'=' * 55}")
        log = out / "generation_log.json"
        json.dump({"timestamp": datetime.now().isoformat(), "mode": self.mode,
                    "elapsed_seconds": round(elapsed, 1), "total_images": num,
                    "success_count": ok_count, "skipped": skipped, "results": results},
                    open(log, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        if self.dedup_enabled:
            print(f"History: {self.history.get('stats', {}).get('total_generated', 0)}  Log: {log}")
        self._generate_gallery(out)
        self.last_run_dir = out
        return {
            "run_dir": str(out),
            "batch_name": out.name,
            "generation_log": str(log),
            "results": results,
        }

    # ── dream ────────────────────────────────────────────────────────

    def cmd_dream(self, description, confirm=True, num=1, mode="combo",
                  max_tags=None, steps=28, cfg_scale=5, sampler="Euler",
                  seed=-1, width=1024, height=1536, negative_prompt=None,
                  min_artists=3, max_artists=5, loras=None):
        # 应用激活的 prompt-only skill
        sid, session = self._session_current()
        active_skill = session.get("active_skill")
        if active_skill:
            print(f"  [skill] 应用: {active_skill['name']}")
            print(f"  [skill] 约束已注入: {active_skill['body'][:80]}...")
            description = f"[Skill: {active_skill['name']}]\n{active_skill['body']}\n\n[用户描述]\n{description}"
            session.pop("active_skill", None)
            self._save_sessions()

        # 蒸馏: 去除结构包装标记, 保留实际内容
        print(f"\n{'─' * 55}")
        print("  [0/4] LLM 正在提炼上下文...")
        print(f"{'─' * 55}")
        raw = description
        description = self._llm().distill_description(description)
        if not description.strip():
            description = raw
        print(f"  → 提炼后: {description[:120]}{'...' if len(description) > 120 else ''}")

        print(f"\n{'─' * 55}")
        print("  [1/4] LLM 正在分析意图...")
        print(f"{'─' * 55}")
        intent = self._llm().analyze_intent(description)
        if not isinstance(intent, dict):
            intent = {"keywords": [], "characters": [], "copyrights": [], "style": description}
        keywords = intent.get("keywords") or [description]
        if isinstance(keywords, str):
            keywords = [keywords]
        print(f"  → 关键词: {', '.join(keywords[:8])}")
        if intent.get("characters"):
            print(f"  → 角色: {', '.join(intent['characters'])}")
        if intent.get("copyrights"):
            print(f"  → 作品: {', '.join(intent['copyrights'])}")
        print(f"  → 风格: {intent.get('style', 'detailed')}")

        print(f"\n{'─' * 55}")
        print("  [2/4] LLM 正在生成标签...")
        print(f"{'─' * 55}")

        known_tags = None
        chars = list(dict.fromkeys(intent.get("characters", [])))
        copyrights = intent.get("copyrights", [])
        if chars:
            # 如果 copyrights 存在，追加 (copyright) 到角色名后以区别同名不同作的角色
            search_names = []
            for c in chars:
                has_suffix = any(f"({cw.lower()})" in c.lower() for cw in copyrights)
                if copyrights and not has_suffix:
                    search_names.append(f"{c} ({copyrights[0]})")
                else:
                    search_names.append(c)
            scraped = self.tag_site.search_characters(search_names)
            if scraped:
                tag_list = []
                seen = set()
                for r in scraped:
                    for t in r.get("tags", []):
                        t = t.strip()
                        if t and t not in seen:
                            tag_list.append(t)
                            seen.add(t)
                known_tags = ", ".join(tag_list)
                print(f"  → 从角色数据库获取到 {len(tag_list)} 个标签: {', '.join(tag_list[:10])}...")

        all_tags = self._llm().generate_tags(
            description, keywords,
            intent.get("characters", []),
            intent.get("copyrights", []),
            max_tags,
            known_tags=known_tags,
        )
        if not isinstance(all_tags, list) or not all_tags:
            all_tags = keywords
        if max_tags:
            all_tags = all_tags[:max_tags]
        print(f"  → 标签 ({len(all_tags)}): {', '.join(all_tags[:12])}")

        print(f"\n{'─' * 55}")
        print("  [3/4] LLM 正在组装 prompt...")
        print(f"{'─' * 55}")
        prompt = self._llm().write_detailed_prompt(
            description, keywords, all_tags,
            intent.get("style", ""),
            intent.get("characters", []),
            intent.get("copyrights", [])
        )
        print(f"  [OK] Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

        local_artists = self._load_artists()
        rec_artists = []
        for kw in keywords:
            rec_artists.extend(self.danbooru.search_artists(kw, local_artists))
        rec_artists = list(dict.fromkeys(rec_artists))
        if loras:
            loras = self._resolve_loras(loras, context=description)

        print(f"\n{'─' * 55}")
        print("  [4/4] 确认参数")
        print(f"{'─' * 55}")
        print(f"  Prompt: {prompt}")
        if rec_artists:
            print(f"  [artist] 推荐艺术家 ({len(rec_artists)}): {', '.join(rec_artists[:5])}...")
        line = f"  生成: {num} 张  |  mode: {mode}"
        if max_tags:
            line += f"  |  max_tags: {max_tags}"
        if loras:
            lora_names = [l.get("_display", l["name"]) if isinstance(l, dict) else l for l in loras[:3]]
            line += f"  |  lora: {', '.join(lora_names)}"
        line += f"  |  {width}x{height}"
        line += f"  |  steps: {steps}  cfg: {cfg_scale}  sampler: {sampler}"
        if negative_prompt:
            line += f"  |  neg: {negative_prompt[:30]}"
        print(line)
        print(f"\n{'─' * 55}")

        if confirm:
            while True:
                cmd = input("  确认生成? [Y/n/edit] ").strip().lower()
                if cmd in ("", "y", "yes"):
                    break
                elif cmd in ("n", "no"):
                    print("  已取消")
                    return
                elif cmd == "edit":
                    new = input("  新 prompt: ").strip()
                    if new:
                        prompt = new
                        print(f"  [OK] 已更新: {prompt}")
                        continue
                else:
                    print("  请输入 Y / n / edit")

        c = self.config
        old = {
            "template": c["prompt"]["template"],
            "neg": c["prompt"]["negative"],
            "width": c["generation"]["width"],
            "height": c["generation"]["height"],
            "steps": c["generation"]["steps"],
            "cfg": c["generation"]["cfg_scale"],
            "seed": c["generation"]["seed"],
            "sampler": c["generation"]["sampler"],
        }
        c["prompt"]["template"] = prompt
        actual_negative_prompt = negative_prompt if negative_prompt is not None else c["prompt"]["negative"]
        if negative_prompt is not None:
            c["prompt"]["negative"] = negative_prompt
        c["generation"]["width"] = width
        c["generation"]["height"] = height
        c["generation"]["steps"] = steps
        c["generation"]["cfg_scale"] = cfg_scale
        c["generation"]["seed"] = seed
        c["generation"]["sampler"] = sampler
        self.cli_args["num"] = num
        self.mode = mode
        self.mode_min = min_artists
        self.mode_max = max_artists
        if rec_artists:
            self.config["artists"]["list_file"] = self._write_temp_artists(rec_artists)
        actual_count = len(rec_artists) if rec_artists else len(self._load_artists())
        if actual_count < self.mode_min:
            if self.mode != "single" and actual_count >= 2:
                self.mode_min = actual_count
                self.mode_max = min(self.mode_max, actual_count)
            else:
                self.mode = "single"
                self.mode_min = 1
                self.mode_max = 1
        run_result = self.run(loras=self._resolve_loras(loras)) or {}
        c["prompt"]["template"] = old["template"]
        c["prompt"]["negative"] = old["neg"]
        c["generation"]["width"] = old["width"]
        c["generation"]["height"] = old["height"]
        c["generation"]["steps"] = old["steps"]
        c["generation"]["cfg_scale"] = old["cfg"]
        c["generation"]["seed"] = old["seed"]
        c["generation"]["sampler"] = old["sampler"]
        self._restore_artists()
        generation = {
            "description": description,
            "prompt": prompt,
            "negative_prompt": actual_negative_prompt,
            "params": {
                "description": description,
                "num": num,
                "mode": mode,
                "max_tags": max_tags,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "sampler": sampler,
                "seed": seed,
                "width": width,
                "height": height,
                "negative_prompt": negative_prompt,
                "min_artists": min_artists,
                "max_artists": max_artists,
                "loras": loras,
            },
            "run": run_result,
        }
        self._save_last_generation(generation)
        return generation

    def _save_last_generation(self, generation):
        sid, session = self._session_current()
        session["last_generation"] = generation
        params = generation.get("params") if isinstance(generation, dict) else None
        if isinstance(params, dict):
            session["last_dream_params"] = params
        self._save_sessions()

    def cmd_generation_info(self, detail="prompt"):
        sid, session = self._session_current()
        state = self.agent.state.get(session)
        artifact = state.get("last_artifact") or {}
        generation = session.get("last_generation") or {}
        if not generation and not artifact:
            print("  [生成] 当前对话还没有记录到上一张生成结果")
            return {"ok": False, "error": "no generation"}
        prompt = artifact.get("prompt") or generation.get("prompt") or ""
        params = generation.get("params") or {}
        run = generation.get("run") or {}
        run_dir = artifact.get("run_dir") or run.get("run_dir")
        if detail in ("summary", "description", "what", "是什么"):
            text = self.agent.state.describe_artifact(artifact or self.agent.state._artifact_from_generation(generation))
            print(f"  [生成] {text}")
            return {"ok": True, "summary": text, "artifact": artifact}
        if detail in ("path", "paths", "output", "folder", "目录"):
            print(f"  [生成] 批次目录: {run_dir or '-'}")
            for item in (run.get("results") or [])[:20]:
                if item.get("info"):
                    print(f"    {item.get('info')}")
            return {"ok": True, "path": run_dir, "artifact": artifact}
        if detail in ("params", "参数"):
            print("  [生成] 参数:")
            for key, value in params.items():
                if value is not None:
                    print(f"    {key}: {value}")
            return {"ok": True, "params": params, "artifact": artifact}
        print("  [生成] 完整提示词:")
        print(f"    {prompt}")
        if generation.get("negative_prompt"):
            print("  [生成] Negative:")
            print(f"    {generation['negative_prompt']}")
        if run_dir:
            print(f"  [生成] 批次目录: {run_dir}")
        return {"ok": True, "prompt": prompt, "artifact": artifact}

    def _git(self, args, check=False):
        return subprocess.run(
            ["git", *args],
            cwd=self.script_dir,
            text=True,
            capture_output=True,
            check=check,
        )

    def cmd_update(self, apply=False, deps=False, remote=None, branch=None):
        update_conf = self.config.get("update", {})
        remote = remote or update_conf.get("remote", "origin")
        branch = branch or update_conf.get("branch", "main")

        try:
            inside = self._git(["rev-parse", "--is-inside-work-tree"])
        except FileNotFoundError:
            print("  [更新] 未找到 git，请先安装 Git")
            return {"ok": False, "error": "git not found"}
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            print("  [更新] 当前目录不是 Git 仓库，无法自动更新")
            return {"ok": False, "error": "not a git repository"}

        remote_url = self._git(["remote", "get-url", remote])
        if remote_url.returncode != 0:
            print(f"  [更新] 未配置远程仓库: {remote}")
            return {"ok": False, "error": f"missing remote: {remote}"}

        print(f"  [更新] 拉取远程信息: {remote}/{branch}")
        fetched = self._git(["fetch", remote, branch])
        if fetched.returncode != 0:
            err = (fetched.stderr or fetched.stdout or "fetch failed").strip()
            print(f"  [更新] fetch 失败: {err}")
            return {"ok": False, "error": err}

        local = self._git(["rev-parse", "HEAD"])
        upstream = self._git(["rev-parse", f"{remote}/{branch}"])
        if local.returncode != 0 or upstream.returncode != 0:
            print(f"  [更新] 无法解析 {remote}/{branch}")
            return {"ok": False, "error": "cannot resolve revision"}
        local_sha = local.stdout.strip()
        upstream_sha = upstream.stdout.strip()

        if local_sha == upstream_sha:
            print("  [更新] 已是最新版本")
            return {"ok": True, "updated": False, "local": local_sha, "remote": upstream_sha}

        commits = self._git(["log", "--oneline", f"HEAD..{remote}/{branch}"])
        print("  [更新] 发现新版本:")
        for line in (commits.stdout or "").splitlines()[:20]:
            print(f"    {line}")
        if not apply:
            print("  [更新] 仅检查更新；执行 update --apply 可拉取最新版本")
            return {"ok": True, "updated": False, "available": True, "local": local_sha, "remote": upstream_sha}

        status = self._git(["status", "--porcelain"])
        dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
        if dirty_lines:
            print("  [更新] 工作区有未提交改动，拒绝自动更新。请先提交或备份:")
            for line in dirty_lines[:30]:
                print(f"    {line}")
            return {"ok": False, "error": "working tree is dirty"}

        print("  [更新] 执行快进更新...")
        pulled = self._git(["pull", "--ff-only", remote, branch])
        if pulled.returncode != 0:
            err = (pulled.stderr or pulled.stdout or "pull failed").strip()
            print(f"  [更新] 更新失败: {err}")
            return {"ok": False, "error": err}
        print((pulled.stdout or "").strip() or "  [更新] 更新完成")

        if deps:
            req = self.script_dir / "requirements.txt"
            if req.exists():
                print("  [更新] 更新 Python 依赖...")
                dep = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req)],
                    cwd=self.script_dir,
                    text=True,
                )
                if dep.returncode != 0:
                    print("  [更新] 依赖更新失败，请手动执行 pip install -r requirements.txt")
                    return {"ok": False, "error": "dependency update failed"}

        return {"ok": True, "updated": True, "local": local_sha, "remote": upstream_sha}

    def _write_temp_artists(self, artists):
        path = self.script_dir / "_dream_artists.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(artists))
        self._dream_original = self.config["artists"]["list_file"]
        return "_dream_artists.txt"

    def _restore_artists(self):
        p = self.script_dir / "_dream_artists.txt"
        if p.exists():
            p.unlink()
        if hasattr(self, "_dream_original"):
            self.config["artists"]["list_file"] = self._dream_original

    # ── tags ─────────────────────────────────────────────────────────

    def cmd_tags(self, keyword, search_type=None):
        results = self.danbooru.search_tags(keyword)
        if not results and re.search(r"[\s,]+", str(keyword or "")):
            seen = set()
            results = []
            for part in re.split(r"[\s,]+", str(keyword or "").strip()):
                if not part:
                    continue
                for item in self.danbooru.search_tags(part):
                    name = item.get("name")
                    if name and name not in seen:
                        results.append(item)
                        seen.add(name)
        if search_type:
            results = [t for t in results if t["type"] == search_type]
        if not results:
            print("  No tags found.")
            return
        by_type = {}
        for t in results:
            by_type.setdefault(t["type"], []).append(t)
        for ttype in ["general", "character", "copyright", "artist"]:
            items = by_type.get(ttype, [])
            if items:
                print(f"\n  [{ttype}] ({len(items)}):")
                for t in items[:10]:
                    print(f"    {t['name']} ({t['count']})")
                if len(items) > 10:
                    print(f"    ... +{len(items) - 10} more")

    def cmd_tagsite(self, *names):
        if not names:
            print("  用法: tagsite <角色名1> [角色名2 ...]")
            return {"query": [], "matches": [], "missing": [], "tags": []}
        matches = []
        missing = []
        all_tags = []
        seen_tags = set()
        for name in names:
            result = self.tag_site.search_character(name)
            if result:
                print(f"\n  [角色] {result['name']}")
                print(f"  [标签] ({len(result['tags'])}): {', '.join(result['tags'])}")
                tags = [str(t).strip() for t in result.get("tags", []) if str(t).strip()]
                for tag in tags:
                    if tag not in seen_tags:
                        all_tags.append(tag)
                        seen_tags.add(tag)
                matches.append({
                    "query": name,
                    "name": result.get("name") or name,
                    "tags": tags,
                    "tag_count": len(tags),
                })
            else:
                print(f"\n  [角色] {name} — 未找到")
                missing.append(name)
        cache_path = self.tag_site.cache_path
        if cache_path.exists():
            size = len(self.tag_site._cache)
            print(f"\n  [缓存] {cache_path.name}: {size} 个角色")
        return {
            "query": list(names),
            "matches": matches,
            "missing": missing,
            "tags": all_tags,
        }

    # ── llm ──────────────────────────────────────────────────────────

    def cmd_llm(self, action, key=None, value=None):
        if action == "test":
            try:
                llm = self._llm()
                resp = llm._call("Say only: OK", "test connection")
                print(f"  [OK] LLM connected. Response: {resp[:50]}")
            except Exception as e:
                print(f"  [ERR] LLM error: {e}")
        elif action == "status":
            c = self.config.get("llm", {})
            print(f"  Provider: {c.get('provider', 'lmstudio')}")
            print(f"  Model:    {c.get('model', 'qwen2.5-7b-instruct')}")
            print(f"  Base URL: {c.get('base_url', 'http://127.0.0.1:1234')}")
            print(f"  API Key:  {'set' if c.get('api_key') else 'null'}")
        elif action == "set" and key:
            self.cmd_config("set", f"llm.{key}", value)
            self._chat_model = None

    # ── LoRA ──────────────────────────────────────────────────────────

    def _fetch_loras(self):
        if self._loras_cache is not None:
            return self._loras_cache
        try:
            base = self.config["sd_api"]["base_url"].rstrip("/")
            resp = self.session.get(f"{base}/sdapi/v1/loras", timeout=10)
            if resp.status_code == 200:
                self._loras_cache = resp.json()
                self._loras = [{"name": l["name"], "alias": l.get("alias", "")} for l in self._loras_cache]
            else:
                self._loras = []
        except Exception:
            self._loras = []
        return self._loras

    def cmd_loras(self, search=None, action=None, name=None, trigger=None):
        if action == "triggers":
            if not self.lora_triggers:
                print("  没有已保存的触发词")
                return
            print(f"  LoRA 触发词 ({len(self.lora_triggers)}):")
            for n, t in self.lora_triggers.items():
                print(f"    {n}  ->  {t}")
            return
        if action == "set-trigger":
            if not name:
                print("  [ERR] 需要指定 LoRA 名称")
                return
            installed = self._fetch_loras()
            match = name
            if installed:
                actual_names = [l["name"] for l in installed]
                if name not in actual_names:
                    from difflib import get_close_matches
                    close = get_close_matches(name, actual_names, n=1, cutoff=0.4)
                    if close:
                        match = close[0]
                        print(f"  [LoRA] '{name}' -> '{match}'")
            if trigger:
                self.lora_triggers[match] = trigger
                self._save_lora_triggers()
                print(f"  [OK] 已设置 '{match}' 的触发词: {trigger}")
            else:
                t = input(f"  请输入 '{match}' 的触发词: ").strip()
                if t:
                    self.lora_triggers[match] = t
                    self._save_lora_triggers()
                    print(f"  [OK] 已保存触发词: {t}")
                else:
                    print("  已取消")
            return

        loras = self._fetch_loras()
        if not loras:
            print("  No LoRAs found (API not running or no LoRAs installed)")
            return
        if search:
            loras = [l for l in loras if search.lower() in l["name"].lower() or search.lower() in l.get("alias", "").lower()]
        print(f"  LoRAs: {len(loras)}")
        for l in loras[:20]:
            alias = f" ({l['alias']})" if l.get("alias") else ""
            name = l['name']
            trigger_info = f"  [触发词: {self.lora_triggers.get(name, '未设置')}]" if name in self.lora_triggers else ""
            print(f"    {name}{alias}{trigger_info}")
        if len(loras) > 20:
            print(f"    ... +{len(loras) - 20} more")

    # ── CLI commands ─────────────────────────────────────────────────

    # ── gallery ──────────────────────────────────────────────────────

    def _generate_gallery(self, out):
        log_path = out / "generation_log.json"
        if not log_path.exists():
            print(f"  [ERR] 未找到日志: {log_path}")
            return False
        with open(log_path, "r", encoding="utf-8") as f:
            log = json.load(f)
        results = log.get("results", [])

        pngs = {}
        for r in results:
            if r.get("success"):
                info = r.get("info", "")
                if info:
                    p = Path(info)
                    if p.suffix == ".png" and p.exists():
                        pngs[r["index"]] = p
        if not pngs:
            print("  [ERR] 没有成功生成的图片")
            return False

        os.makedirs(out / "_thumbs", exist_ok=True)
        thumb_data = {}
        for idx, p in pngs.items():
            thumb_path = out / "_thumbs" / p.name
            if not thumb_path.exists():
                try:
                    img = Image.open(p)
                    img.thumbnail((300, 450), Image.LANCZOS)
                    img.save(thumb_path, "PNG")
                    thumb_data[idx] = thumb_path.name
                except Exception:
                    thumb_data[idx] = p.name
            else:
                thumb_data[idx] = thumb_path.name

        cards_html = ""
        for r in results:
            if not r.get("success"):
                continue
            idx = r["index"]
            fn = thumb_data.get(idx, "")
            if not fn:
                continue
            artists = ", ".join(r.get("artists", []))
            prompt = r.get("prompt", "")
            png_name = pngs[idx].name
            cards_html += (
                f'  <div class="card">'
                f'<a href="{png_name}" target="_blank">'
                f'<img src="_thumbs/{fn}" loading="lazy"></a>'
                f'<div class="info">'
                f'<div class="artists">{artists}</div>'
                f'<div class="prompt">{prompt}</div>'
                f'</div></div>\n'
            )

        ts = log.get("timestamp", "")[:19]
        mode = log.get("mode", "?")
        succ = log.get("success_count", 0)
        total = log.get("total_images", 0)
        elapsed = log.get("elapsed_seconds", 0)
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{out.name} - SD Gallery</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:#1a1a1a;color:#e0e0e0;padding:20px}}
h1{{font-size:18px;margin-bottom:4px}}
.meta{{font-size:13px;color:#999;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}}
.card{{background:#2a2a2a;border-radius:8px;overflow:hidden;transition:transform .15s}}
.card:hover{{transform:translateY(-2px)}}
.card img{{width:100%;display:block}}
.info{{padding:10px;font-size:12px}}
.artists{{color:#7af;margin-bottom:4px;word-break:break-all}}
.prompt{{color:#999;word-break:break-all;max-height:60px;overflow:hidden}}
</style>
</head>
<body>
<h1>{out.name}</h1>
<div class="meta">{ts} | {mode} | {succ}/{total} success | {elapsed}s</div>
<div class="grid">
{cards_html}</div>
</body>
</html>"""
        with open(out / "index.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [Gallery] {out / 'index.html'}")
        return True

    def cmd_gallery(self, run_name=None, list_only=False, regenerate=False, open_after=True):
        base = self.script_dir / self.config["output"]["base_dir"]
        if not base.exists():
            print("  没有输出目录")
            return
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        if list_only:
            print(f"  共有 {len(dirs)} 个批次:")
            for d in dirs:
                html_exists = (d / "index.html").exists()
                tag = " [有画廊]" if html_exists else ""
                log_path = d / "generation_log.json"
                ts = ""
                if log_path.exists():
                    try:
                        log = json.load(open(log_path, "r", encoding="utf-8"))
                        ts = log.get("timestamp", "")[:19]
                    except Exception:
                        pass
                print(f"    {d.name}{tag}  {ts}")
            return
        if run_name:
            target = base / run_name
            if not target.exists() or not target.is_dir():
                print(f"  [ERR] 未找到批次: {run_name}")
                return
        else:
            target = dirs[0] if dirs else None
            if not target:
                print("  没有批次")
                return
        if regenerate or not (target / "index.html").exists():
            self._generate_gallery(target)
        p = target / "index.html"
        if p.exists() and open_after:
            self._open_gallery_file(p)

    def _open_gallery_file(self, path):
        path = Path(path).resolve()
        url = path.as_uri()
        print(f"  [Gallery] {path}")
        print(f"  打开: {url}")
        if sys.platform == "win32":
            os.startfile(path)
            return
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    def cmd_webui(self, host="127.0.0.1", port=7861, background=False):
        if getattr(self, "_webui_running", False):
            print(f"  [OK] WebUI 已在运行: http://{self._webui_host}:{self._webui_port}")
            return
        from webui import create_app, JobQueue, register_routes
        app = create_app(self)
        job_queue = JobQueue(self)
        register_routes(app, job_queue)
        if background:
            import threading
            t = threading.Thread(target=app.run,
                                 kwargs={"host": host, "port": port,
                                         "debug": False, "use_reloader": False},
                                 daemon=True)
            t.start()
            self._webui_running = True
            self._webui_host = host
            self._webui_port = port
            print(f"  [OK] WebUI 已在后台启动: http://{host}:{port}")
            print(f"  在浏览器打开链接访问，按 Ctrl+C 停止 CLI 会一并停止 WebUI")
        else:
            self._webui_running = True
            self._webui_host = host
            self._webui_port = port
            print(f"  WebUI 启动: http://{host}:{port}")
            print(f"  按 Ctrl+C 停止")
            app.run(host=host, port=port, debug=False, use_reloader=False)

    def cmd_critique(self, path="last", expected=None):
        try:
            vc = self._get_vc()
        except RuntimeError as e:
            print(f"  [ERR] {e}")
            return
        if path == "last":
            if self.last_run_dir:
                pngs = sorted(Path(self.last_run_dir).glob("*.png"))
                if not pngs:
                    print("  [ERR] 最后批次没有 PNG 文件")
                    return
                path = str(pngs[0])
            else:
                print("  [ERR] 还没有生成记录, 无法使用 last")
                return
        print(f"\n{'─' * 55}")
        print("  苏丹娜正在审视图片...")
        print(f"{'─' * 55}")
        print(f"  图片: {path}")
        r = vc.critique(path, expected_desc=expected)
        if not r["ok"]:
            print(f"  [ERR] 识图失败: {r['error']}")
            return
        print(f"\n  ═══ 视觉分析 ═══")
        for line in r["text"].split("\n"):
            print(f"  {line}")
        print(f"  ════════════════")

    def cmd_telegram(self, action="status", token=None, block=False):
        if action == "stop":
            if self._telegram_running and self._telegram_bot:
                self._telegram_bot.stop()
                self._telegram_bot = None
                print("  [OK] Telegram 机器人已停止")
            else:
                print("  [OK] Telegram 机器人未运行")
            self._telegram_running = False
            return

        if action == "start":
            if self._telegram_running:
                print("  [OK] Telegram 机器人已在运行")
                return
            tg_conf = self.config.get("telegram", {})
            bot_token = token or tg_conf.get("token", "")
            if not bot_token:
                print("  [ERR] 未配置 Telegram token")
                print("  请设定: config_set telegram.token 你的token")
                return
            allowed = tg_conf.get("allowed_users", [])
            self._telegram_bot = TelegramBot(
                self,
                bot_token,
                allowed,
                proxy_url=tg_conf.get("proxy_url"),
                connect_timeout=int(tg_conf.get("connect_timeout", 20)),
                read_timeout=int(tg_conf.get("read_timeout", 30)),
                write_timeout=int(tg_conf.get("write_timeout", 30)),
            )
            import threading
            t = threading.Thread(target=self._telegram_bot.run, daemon=True)
            t.start()
            self._telegram_running = True
            print("  [OK] Telegram 机器人已后台启动")
            if block:
                print("  按 Ctrl+C 停止")
                try:
                    while self._telegram_running:
                        time.sleep(3600)
                except KeyboardInterrupt:
                    print("  正在停止 Telegram 机器人...")
                finally:
                    self._cleanup_telegram()
            return

        # status
        if self._telegram_running:
            print("  [OK] Telegram 机器人运行中")
            tg_conf = self.config.get("telegram", {})
            token_ok = bool(tg_conf.get("token", ""))
            users = tg_conf.get("allowed_users", [])
            print(f"  Token: {'已配置' if token_ok else '未配置'}")
            print(f"  允许用户: {users if users else '全部'}")
            print(f"  代理: {'已配置' if tg_conf.get('proxy_url') else '未配置'}")
            if self._telegram_bot and self._telegram_bot.last_error:
                print(f"  最近错误: {self._telegram_bot.last_error[:200]}")
        else:
            print("  [OK] Telegram 机器人未启动")
            print("  启动: telegram start")
            tg_conf = self.config.get("telegram", {})
            if tg_conf.get("token"):
                print("  注: token 已配置，直接启动即可")

    def _cleanup_telegram(self):
        if self._telegram_running and self._telegram_bot:
            try:
                self._telegram_bot.stop()
            except Exception:
                pass
            self._telegram_bot = None
            self._telegram_running = False

    # ── 文件操作 (白名单) ──────────────────────────────────────────

    _ALLOWED_FILES = {
        "config.yaml", "tag_cache.json", "lora_triggers.json",
        "sessions.json", "history.json",
    }
    _MAX_READ_SIZE = 1 * 1024 * 1024
    _MAX_WRITE_SIZE = 500 * 1024

    def _resolve_path(self, path, write=False):
        if not path:
            return None, "path 不能为空"
        p = Path(path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            if ".." in p.parts:
                return None, f"路径含 .. 不允许: {path}"
            resolved = (self.script_dir / p).resolve()
        try:
            resolved.relative_to(self.script_dir.resolve())
        except ValueError:
            return None, f"路径超出项目目录: {path}"
        script_root = self.script_dir.resolve()
        outputs_dir = (self.script_dir / self.config["output"]["base_dir"]).resolve()
        try:
            resolved.relative_to(outputs_dir)
            return resolved, None
        except ValueError:
            pass
        skills_dir = (self.script_dir / "skills").resolve()
        try:
            resolved.relative_to(skills_dir)
            if resolved == skills_dir:
                return None, f"不允许直接操作 skills 根目录"
            if resolved.is_dir() and re.match(r"^[a-zA-Z0-9_\-]+$", resolved.name):
                sk_md = resolved / "SKILL.md"
                return sk_md, None
            if resolved.name == "SKILL.md" and re.match(r"^[a-zA-Z0-9_\-]+$", resolved.parent.name):
                return resolved, None
        except (ValueError, OSError):
            pass
        if resolved.parent == script_root and resolved.name in self._ALLOWED_FILES:
            return resolved, None
        return None, f"路径不在白名单: {path}"

    _BINARY_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico",
        ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flac", ".wav", ".ogg",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".tar", ".gz", ".7z", ".rar", ".iso",
        ".exe", ".dll", ".so", ".dylib", ".bin",
    }

    def cmd_file_read(self, path, start_line=None, max_lines=None):
        resolved, err = self._resolve_path(path)
        if err:
            print(f"  [ERR] {err}")
            return
        if not resolved.exists():
            print(f"  [ERR] 不存在: {path}")
            return
        if resolved.stat().st_size > self._MAX_READ_SIZE:
            print(f"  [ERR] 文件超过 {self._MAX_READ_SIZE // 1024} KB 限制")
            return
        ext = resolved.suffix.lower()
        if ext in self._BINARY_EXTS:
            sz = resolved.stat().st_size
            print(f"  [二进] {path} ({sz} 字节, 类型: {ext})")
            print(f"  → 二进制文件不解码。用 file_list 找文本文件")
            return
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"  [ERR] 读取失败: {e}")
            return
        if start_line is not None or max_lines is not None:
            s = int(start_line or 0)
            n = int(max_lines or len(lines))
            lines = lines[s:s + n]
        print(f"  [文件] {path} ({resolved.stat().st_size} 字节, {len(lines)} 行)")
        for i, line in enumerate(lines[:200], 1):
            print(f"  {i:4d}  {line.rstrip()}")
        if len(lines) > 200:
            print(f"  ... +{len(lines) - 200} 行")

    def cmd_file_write(self, path, content, confirm_yes=False):
        resolved, err = self._resolve_path(path, write=True)
        if err:
            print(f"  [ERR] {err}")
            return
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2, ensure_ascii=False)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > self._MAX_WRITE_SIZE:
            print(f"  [ERR] 内容超过 {self._MAX_WRITE_SIZE // 1024} KB 限制")
            return
        existed = resolved.exists()
        if not confirm_yes and existed:
            print(f"  [注意] {path} 已存在 ({resolved.stat().st_size} 字节)")
            ans = input("  覆盖? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("  已取消")
                return
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            verb = "覆盖" if existed else "创建"
            print(f"  [OK] {verb} {path} ({content_bytes} 字节)")
        except Exception as e:
            print(f"  [ERR] 写入失败: {e}")

    def cmd_file_list(self, path=".", pattern=None, max_count=100):
        base, err = self._resolve_path(path or ".")
        if err:
            print(f"  [ERR] {err}")
            return
        if not base.exists():
            print(f"  [ERR] 不存在: {path}")
            return
        if base.is_file():
            print(f"  [文件] {path} ({base.stat().st_size} 字节)")
            return
        if pattern:
            matches = sorted(base.glob(pattern))
        else:
            matches = sorted(base.iterdir())
        total = len(matches)
        shown = matches[:max_count]
        print(f"  [目录] {path} ({total} 项)")
        for p in shown:
            if p.is_dir():
                print(f"    [D] {p.name}/")
            else:
                sz = p.stat().st_size
                print(f"    [F] {p.name}  ({sz} B)")
        if total > max_count:
            print(f"    ... +{total - max_count} 项")

    def cmd_file_delete(self, path, confirm_yes=False):
        resolved, err = self._resolve_path(path)
        if err:
            print(f"  [ERR] {err}")
            return
        if not resolved.exists():
            print(f"  [ERR] 不存在: {path}")
            return
        if resolved.is_dir():
            print(f"  [ERR] 不支持删除目录: {path}")
            return
        sz = resolved.stat().st_size
        if not confirm_yes:
            print(f"  [确认] 即将删除 {path} ({sz} 字节)")
            ans = input("  确认删除? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("  已取消")
                return
        try:
            resolved.unlink()
            print(f"  [OK] 已删除 {path}")
        except Exception as e:
            print(f"  [ERR] 删除失败: {e}")

    def cmd_file_find(self, pattern, contains=None, max_count=20):
        files = list(self.script_dir.glob(pattern))
        files = [f for f in files if f.is_file()]
        if contains:
            needle = contains.lower()
            results = []
            for f in files:
                try:
                    if f.stat().st_size > self._MAX_READ_SIZE:
                        continue
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except Exception:
                    continue
                if needle in text.lower():
                    results.append(f)
            files = results
        print(f"  [搜索] pattern={pattern!r} contains={contains!r} → {len(files)} 个文件")
        for f in files[:max_count]:
            try:
                rel = f.relative_to(self.script_dir)
            except ValueError:
                rel = f
            print(f"    {rel}  ({f.stat().st_size} B)")
        if len(files) > max_count:
            print(f"    ... +{len(files) - max_count} 项")

    # ── Skills (SKILL.md prompt 模板) ────────────────────────────────

    _SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

    def skills_dir(self):
        return self.script_dir / "skills"

    def _resolve_skill_path(self, name):
        if not name or not self._SKILL_NAME_RE.match(name):
            return None, f"skill 名非法: {name!r} (只允许字母数字下划线连字符)"
        skill_md = self.skills_dir() / name / "SKILL.md"
        if not skill_md.exists():
            return None, f"skill 不存在: {name}"
        try:
            skill_md.resolve().relative_to(self.skills_dir().resolve())
        except ValueError:
            return None, f"skill 路径异常: {name}"
        return skill_md, None

    def _skills_summary(self):
        sdir = self.skills_dir()
        if not sdir.exists():
            return "无 (skills/ 目录不存在)"
        skills = sorted([d for d in sdir.iterdir() if d.is_dir()])
        if not skills:
            return "无"
        names = []
        for d in skills:
            md = d / "SKILL.md"
            if not md.exists():
                continue
            parsed = self._parse_skill(md)
            if parsed.get("_error"):
                continue
            desc = f"{parsed['name']}({parsed['description']})" if parsed["description"] else parsed["name"]
            names.append(desc)
        if not names:
            return "无"
        return ", ".join(names)

    def _parse_skill(self, skill_md_path):
        text = skill_md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {
                "name": skill_md_path.parent.name,
                "description": "",
                "triggers": [],
                "chain_template": None,
                "body": text,
                "_error": None,
            }
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {"_error": "SKILL.md frontmatter 格式错误"}
        try:
            import yaml
            meta = yaml.safe_load(parts[1]) or {}
        except Exception as e:
            return {"_error": f"YAML 解析失败: {e}"}
        body = parts[2].strip()
        return {
            "name": meta.get("name", skill_md_path.parent.name),
            "description": meta.get("description", ""),
            "triggers": meta.get("triggers", []),
            "chain_template": meta.get("chain_template"),
            "body": body,
            "_error": None,
        }

    def cmd_skill_list(self):
        sdir = self.skills_dir()
        if not sdir.exists():
            print("  [OK] skills 目录不存在")
            return
        skills = sorted([d for d in sdir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])
        print(f"  [skills] 共 {len(skills)} 个")
        for d in skills:
            md = d / "SKILL.md"
            parsed = self._parse_skill(md)
            err = parsed.get("_error")
            if err:
                print(f"    [E] {d.name}/  ({err})")
                continue
            print(f"    [{parsed['name']}] {parsed['description']}")
            for trig in parsed["triggers"][:3]:
                print(f"      触发: {trig}")
            if parsed["chain_template"]:
                print(f"      链式: 是")
            else:
                print(f"      链式: 否 (纯 prompt)")

    def cmd_skill_load(self, name, params=None):
        skill_md, err = self._resolve_skill_path(name)
        if err:
            print(f"  [ERR] {err}")
            return None
        parsed = self._parse_skill(skill_md)
        perr = parsed.get("_error")
        if perr:
            print(f"  [ERR] {perr}")
            return None
        print(f"  [skill] {parsed['name']}: {parsed['description']}")

        # prompt-only 类型：保存到 session 供后续 dream 注入
        if not parsed["chain_template"]:
            sid, session = self._session_current()
            session["active_skill"] = {
                "name": parsed["name"],
                "body": parsed["body"],
                "description": parsed["description"],
            }
            self._save_sessions()
            print(f"  → 已激活 prompt-only skill: {parsed['name']}")
            print(f"  → 后续 dream/action 会自动应用此 skill 的约束")
            return {"type": "prompt_only", "skill": parsed, "params": params or {}}

        # chain 类型：解析+合并 params, 排队到 _pending_skill_chain
        # 不在这里执行, 由外层 _react_loop 接管, 避免重复执行
        try:
            chain = json.loads(parsed["chain_template"])
        except json.JSONDecodeError as e:
            print(f"  [ERR] chain_template JSON 解析失败: {e}")
            return None
        if params:
            for step in chain:
                step_params = step.get("params", {})
                # 合并策略:
                #   description → 追加 (skill 基标签 + 用户描述, LLM 需要完整上下文)
                #   其他字段 (loras/num/mode) → params 优先
                for k, v in params.items():
                    if v is not None:
                        if k == "description":
                            base = step_params.get("description", "")
                            if base and v and v not in base:
                                step_params["description"] = base + ", " + v
                            elif v:
                                step_params["description"] = v
                        else:
                            step_params[k] = v
                step["params"] = step_params
        print(f"  [链] {len(chain)} 步")
        for i, step in enumerate(chain, 1):
            print(f"    {i}. {step['action']} {step.get('params', {})}")
        self._pending_skill_chain = list(chain)
        self._pending_skill_body = parsed.get("body", "")
        return {"type": "chain", "chain": chain, "skill": parsed}

    SKILL_GUIDE = """\
─── SKILL.md 编写指导 ───

SKILL.md 是带 YAML frontmatter 的 Markdown 文件, 格式:

---
name: my_skill              ← 必填, 字母数字下划线连字符
description: 一句话说明     ← 必填, LLM 据此匹配
triggers:                   ← 选填, 触发关键词列表
  - 触发词1
  - 触发词2
chain_template: |          ← 选填, 有此字段才走"链式执行"模式
  [{"action": "dream", "params": {"description": "...", "loras": [...]}}]
---

# 详细说明 (Markdown 正文)

## 步骤
1. xxx
2. yyy

⚠ 关键注意事项:
1. YAML frontmatter 必须用三个减号 --- 包围, 否则被识别为纯 prompt 类型
2. chain_template 必须是合法 JSON 数组, 每元素含 action + params
3. LoRA 的 name 字段必须用 SD WebUI /sdapi/v1/loras 里的实际名字
   → 用 /tagsite 看角色标签, 用 loras list 看 LoRA 真名
4. 触发词尽量多列同义词, 便于苏丹娜自动匹配

## 两种模式
A) chain_template 有内容 → 加载时自动按步骤执行
B) 无 chain_template → 内容注入到下次 dream 的描述, 一次性约束

## 示例 (复制可用)

### 纯 prompt 类型 (简单约束)
---
name: portrait_quality
description: 强制高质量肖像, 单人构图
triggers:
  - 肖像
  - 高质量
---

## 约束
- 必须 single 模式, 1girl, upper body, masterpiece
- 不要有多个角色

### 链式类型 (自动执行流程)
---
name: my_workflow
description: 自动查角色 tag 然后画 1 张
triggers:
  - 关键词
chain_template: |
  [{"action": "tagsite", "params": {"names": ["角色名"]}},
   {"action": "dream", "params": {"description": "角色名", "loras": [{"name": "xxx", "weight": 0.8}]}}]
---

## 流程
1. 查角色标签
2. 用对应 LoRA 生成

─── 创建方法 ───

CLI:        python sdbot.py skill_create ...
Agent:      让苏丹娜说"创建一个 xxx skill ..."
WebUI:      /skill_create (TODO)
帮助:       /skill 打印本指南
"""

    def cmd_skill_create(self, name, description="", triggers=None, chain_template=None, body="", show_help=False):
        if show_help:
            print(self.SKILL_GUIDE)
            return
        if not name:
            print("  [ERR] skill 名不能为空")
            print(f"  提示: 输入 /skill 查看编写指导")
            return
        if not self._SKILL_NAME_RE.match(name):
            print(f"  [ERR] skill 名非法: {name!r} (只允许字母数字下划线连字符)")
            return
        if not description:
            print(f"  [ERR] description 不能为空 (LLM 靠它匹配)")
            print(f"  提示: 输入 /skill 查看编写指导")
            return
        overwrite = bool((self.skills_dir() / name / "SKILL.md").exists())
        ok, msg = self._write_skill_file(name, description=description, triggers=triggers or [],
                                          chain_template=chain_template, body=body, overwrite=overwrite)
        if not ok:
            print(f"  [ERR] {msg}")
            return
        if overwrite:
            print(f"  [OK] 已覆盖 skill: skills/{name}/SKILL.md")
        else:
            print(f"  [OK] 已创建 skill: skills/{name}/SKILL.md ({len(msg)} 字节)")
            print(f"  描述: {description}")
            print(f"  触发: {', '.join(triggers or []) or '(无)'}")
            print(f"  链式: {'是' if chain_template else '否 (纯 prompt)'}")
            print(f"  → 已加入 skills/, 苏丹娜下次会自动调用")

    def _write_skill_file(self, name, description="", triggers=None, chain_template=None, body="", overwrite=False):
        if isinstance(chain_template, str):
            chain_template = chain_template.strip()
            if chain_template.startswith("```"):
                chain_template = chain_template.split("\n", 1)[1] if "\n" in chain_template else chain_template[3:]
                if chain_template.endswith("```"):
                    chain_template = chain_template[:-3]
                chain_template = chain_template.strip()
        if not self._SKILL_NAME_RE.match(name):
            return False, f"skill 名非法: {name!r} (只允许字母数字下划线连字符)"
        skill_dir = self.skills_dir() / name
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists() and not overwrite:
            return False, f"skill 已存在: {name} (传 overwrite=True 可覆盖)"
        meta = {
            "name": name,
            "description": description,
            "triggers": triggers or [],
        }
        if chain_template:
            try:
                chain = json.loads(chain_template)
                if not isinstance(chain, list):
                    return False, f"chain_template 必须是 JSON 数组, 不是 {type(chain).__name__}"
                for i, step in enumerate(chain):
                    if not isinstance(step, dict) or "action" not in step:
                        return False, f"chain_template 第 {i+1} 步缺少 action 字段"
                meta["chain_template"] = chain_template
            except json.JSONDecodeError as e:
                return False, f"chain_template JSON 解析失败: {e}"
        try:
            import yaml
            front = yaml.dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
        except Exception as e:
            return False, f"YAML 序列化失败: {e}"
        if not body:
            body = f"## 描述\n\n{description}\n"
        content = f"---\n{front}---\n\n# {name}\n\n{body}\n"
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_md.write_text(content, encoding="utf-8")
            return True, content
        except Exception as e:
            return False, f"写入失败: {e}"

    # ── 网页抓取 ──────────────────────────────────────────

    def cmd_web_fetch(self, url, format="text", max_length=None):
        if not url:
            print("  [ERR] url 不能为空")
            return
        result = self.web.fetch(url, format=format, max_length=max_length)
        if not result.get("ok"):
            print(f"  [ERR] {result.get('error', 'unknown')}")
            return
        print(f"  [web] {result['url']}")
        if result.get("final_url") and result["final_url"] != result["url"]:
            print(f"  → {result['final_url']}")
        print(f"  [状态] {result['status']}  [类型] {result['content_type']}  [编码] {result['encoding']}")
        if result.get("title"):
            print(f"  [标题] {result['title']}")
        content = result["content"]
        total = len(content)
        print(f"  [内容] {total} 字符" + (" (已截断)" if result.get("truncated") else ""))
        preview = content[:500]
        print(f"  ──── 预览 ────")
        for line in preview.splitlines():
            print(f"  {line}")
        if total > 500:
            print(f"  ... +{total - 500} 字符 (用 max_length 参数获取完整)")

    def cmd_status(self):
        artists = self._load_artists()
        combos = self.history.get("combos", {})
        loras = self._fetch_loras()
        print(f"API:        {self.api_url}")
        print(f"Mode:       {self.mode} ({self.mode_min}-{self.mode_max})  Dedup: {'on' if self.dedup_enabled else 'off'}  sim: {self.similarity_filter}")
        print(f"Artists:    {len(artists)}  Generated: {len(combos)}  LoRAs: {len(loras)}")
        print(f"Resolution: {self.config['generation']['width']}x{self.config['generation']['height']}  Sampler: {self.config['generation'].get('sampler','Euler a')}")
        o = self.script_dir / self.config["output"]["base_dir"]
        if o.exists():
            dirs = sorted(d for d in o.iterdir() if d.is_dir())
            print(f"Outputs:    {len(dirs)} runs")
            for d in dirs[-3:]:
                print(f"  {d.name}")

    def cmd_history(self, last=20, search=None):
        combos = self.history.get("combos", {})
        if not combos:
            print("No history yet.")
            return
        items = sorted(combos.values(), key=lambda x: x.get("generated_at", ""), reverse=True)
        if search:
            items = [i for i in items if search.lower() in ",".join(i.get("artists", [])).lower()]
        for item in items[:last]:
            print(f"  {item.get('generated_at','?')[:19]}  {', '.join(item.get('artists', []))[:80]}")

    def cmd_artists(self, search=None, count_only=False):
        a = self._load_artists()
        if count_only:
            print(f"{len(a)} artists")
            return
        if search:
            a = [x for x in a if search.lower() in x.lower()]
        for x in a:
            dup = " [gen]" if self.dedup_enabled and self._is_duplicate([x]) else ""
            print(f"  {x}{dup}")

    def cmd_config(self, action="show", key=None, value=None):
        if action == "show":
            print(yaml.dump(self.config, allow_unicode=True, default_flow_style=False))
        elif action == "set" and key and value is not None:
            keys = key.split(".")
            obj = self.config
            for k in keys[:-1]:
                obj = obj.setdefault(k, {})
            try:
                v = int(value)
            except ValueError:
                v = {"true": True, "false": False, "null": None}.get(value.lower(), value)
            obj[keys[-1]] = v
            self._save_config()
            print(f"Set {key} = {v}")
        elif action == "get" and key:
            obj = self.config
            for k in key.split("."):
                obj = obj.get(k, {})
            print(obj)

    def cmd_clear(self, target="history"):
        if not self.safety.is_safe_clear_target(target):
            print(f"  [ERR] 不安全的清理目标: {target}")
            return
        if target == "history":
            if self.history_path.exists():
                self.history_path.unlink()
                self.history = {}
            print("History cleared.")
        elif target == "outputs":
            o = (self.script_dir / self.config["output"]["base_dir"]).resolve()
            root = self.script_dir.resolve()
            try:
                o.relative_to(root)
            except ValueError:
                print(f"  [ERR] outputs 路径不在项目目录内: {o}")
                return
            if o == root:
                print(f"  [ERR] 拒绝清理项目根目录: {o}")
                return
            if not o.parent.samefile(root):
                print(f"  [ERR] outputs 必须直接位于项目根目录下: {o}")
                return
            if o.exists():
                for child in o.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            print("Outputs cleared.")
        else:
            print(f"  [ERR] 不支持的清理目标: {target}")

    # ── Memory ────────────────────────────────────────────────────────

    @property
    def _memory_namespace(self):
        ns = getattr(self, "_current_memory_ns", None)
        if ns:
            return ns
        return "local"

    def cmd_memory_set(self, key, value):
        if not key:
            print("  [ERR] key 不能为空")
            return
        ns = self._memory_namespace
        self.sessions.setdefault("global_memory", {}).setdefault(ns, {})[key] = str(value)
        self._save_sessions()
        print(f"  [记忆] 已保存: {key} = {str(value)[:80]}")

    def cmd_memory_get(self, key):
        if not key:
            print("  [ERR] key 不能为空")
            return
        ns = self._memory_namespace
        val = self.sessions.get("global_memory", {}).get(ns, {}).get(key)
        if val is not None:
            print(f"  [记忆] {key} = {str(val)[:200]}")
        else:
            print(f"  [记忆] 未找到: {key}")

    def cmd_memory_forget(self, key, confirm=None):
        if not key:
            print("  [ERR] key 不能为空")
            return
        if confirm != "yes":
            print(f"  [确认] 要删除记忆 '{key}'? 设置 confirm=yes 确认")
            return
        ns = self._memory_namespace
        store = self.sessions.setdefault("global_memory", {}).setdefault(ns, {})
        if store.pop(key, None) is not None:
            self._save_sessions()
            print(f"  [记忆] 已删除: {key}")
        else:
            print(f"  [记忆] 未找到: {key}")

    def cmd_memory_list(self):
        ns = self._memory_namespace
        mem = self.sessions.get("global_memory", {}).get(ns, {})
        if not mem:
            print("  [记忆] 暂无保存的记忆")
            return
        print(f"  [记忆] 共 {len(mem)} 条:")
        for k, v in mem.items():
            print(f"    {k}: {str(v)[:80]}")

    # ── Agent ────────────────────────────────────────────────────────

    def _agent_system_prompt(self):
        artists = self._load_artists()
        combos = self.history.get("combos", {})
        sid, session = self._session_current()
        artist_count = len(artists)
        gen_count = len(combos)
        dedup_str = "开启" if self.dedup_enabled else "关闭"
        loras = self._fetch_loras()
        actions = list(getattr(self, "tool_registry", None).names()) if getattr(self, "tool_registry", None) else []
        actions.append("chat")
        ns = self._memory_namespace
        memory = self.sessions.get("global_memory", {}).get(ns, {})
        memory_text = "\n".join(f"  {k}: {str(v)[:80]}" for k, v in memory.items()) if memory else "（暂无，我会随着聊天自然记住你）"
        summary = session.get("summary", "")
        return (
            "你叫苏丹娜，是一个绿色长发、头顶猫耳的专业 AI 绘图与系统助手。"
            "默认专业、清晰、简洁；技术问题、配置、报错、模型、Telegram、绘图任务必须正常回答。"
            "只有用户明确要求调情、成人角色扮演或猫娘互动时，才进入色情猫娘语气。"
            "不要在普通问候、报错、模型切换、搜索角色或绘图请求里主动色情化。\n\n"

            "## 当前系统状态\n"
            f"- 当前对话: {session['name']}\n"
            f"- 对话模型: {self._selection_model_name('chat')}\n"
            f"- 识图模型: {self._selection_model_name('vision')}\n"
            f"- 模式: {self.mode} ({self.mode_min}-{self.mode_max} 个艺术家)\n"
            f"- 去重: {dedup_str}\n"
            f"- 艺术家库: {artist_count} 人\n"
            f"- 已生成: {gen_count} 张\n"
            f"- API: {self.api_url}\n"
            f"- LoRAs: {len(loras)} 个\n"
            f"- Skills: {self._skills_summary()}\n\n"

            "## 苏丹娜记忆中的你\n"
            f"{memory_text}\n\n"

            "## 对话摘要\n"
            f"{summary or '（当前对话较短，暂无摘要）'}\n\n"

            "## 输出格式\n"
            "只能输出纯 JSON，不要输出解释性文本。\n"
            "单步: {\"reply\": \"给用户看的中文回复\", \"action\": \"操作名\", \"params\": {...}}\n"
            "多步: {\"reply\": \"给用户看的中文回复\", \"chain\": [{\"action\": \"操作名\", \"params\": {...}}]}\n"
            "多选: {\"reply\": \"给用户看的中文说明\", \"choices\": [{\"label\": \"选项名\", \"description\": \"说明\", \"chain\": [{\"action\": \"操作名\", \"params\": {...}}]}]}\n"
            "纯聊天: {\"reply\": \"...\", \"action\": \"chat\", \"params\": {}}\n\n"

            "## 可用操作\n"
            f"{', '.join(actions)}\n"
            "参数细节由系统 schema 校验；你只需填必要且明确的 params，不要编造无关参数。\n\n"

            "## 路由原则\n"
            "- 用户说画图/生成图片/涩图: action=dream。description 必须保留用户指定的人物、作品、动作和风格。\n"
            "- 用户说搜索/查角色/tag/标签: action=tagsite，不要用 dream。\n"
            "- 用户说模型状态/当前模型/用的什么模型: action=models, params.action=status。\n"
            "- 用户说切换对话模型: action=models, params.action=switch, role=chat。\n"
            "- 用户说切换识图/视觉模型: action=models, params.action=switch, role=vision。\n"
            "- 用户说再画/继续/上一张加内容: 基于系统提供的结构化 last_dream_params 处理，不要从完整历史脑补。\n"
            "- 具体角色/人物/作品绘图请求必须先用 character_resolve 解析角色；高置信解析后再生成 choices；不得在未确认时脑补角色设定。\n"
            "- 不需要角色资料的绘图请求先返回 prompt choices；choices 的详细规则以当前 intent schema 为准。\n"
            "- 用户要求多个连续任务时才使用 chain；每次不要自动链式生成多个 dream，除非用户明确要求。\n"
            "- skill 明显匹配时先 skill_load，再继续 dream 或其他任务。\n\n"

            "## 主动记忆\n"
            "当用户告诉你名字、偏好、常用设置、注意事项时，主动在 chain 中插入 memory_set 保存到跨会话记忆。\n"
            "发现用户重复使用某个参数（LoRA、sampler、模式）时也值得记住。\n"
            "用户说忘了什么时用 memory_forget（需 confirm=yes）。\n"
            "询问你还记得什么时用 memory_get 或 memory_list。\n\n"

            "## 利用记忆\n"
            "\"苏丹娜记忆中的你\"包含已知信息，回复时自然融入。\n"
            "不需要喊出\"我记得\"，直接按记忆行动。\n"
            "例如记忆中有 preferred_style=portrait → dream 时默认 single 模式。\n"
            "记忆中的信息跨会话有效，但当前请求没有提到的东西不要脑补。\n\n"
            "生成 prompt choices 时要自然利用用户偏好，例如偏好的构图、强度、内容倾向或禁忌；"
            "但不得违背用户当前明确指定的 SFW/NSFW、角色、服装、动作和参数。\n\n"

            "## 严格约束\n"
            "- 严格按用户原意处理，不准替换角色，不准添加用户没提到的服装/场景/动作。\n"
            "- 用户指定角色名时，description 必须保留该角色名；不要改成猫娘、女孩、小姨等通用主体。\n"
            "- 默认每条消息是独立请求；没有明确说刚才/上一张/继续/改成/换成时，不要继承上一轮主题。\n"
            "- 不要提及内部处理、base64、解码、系统提示或 schema。\n"
            "- 如果缺少必要信息且无法从结构化上下文补全，用 chat 反问。"
        )

    def _agent_process(self, user_input, use_context=None, source="cli", user_id=None):
        if source == "telegram" and user_id:
            self._current_memory_ns = f"tg_{user_id}"
        else:
            self._current_memory_ns = "local"
        return self.agent.process(user_input, source=source, use_context=use_context)

    def _step_desc(self, action, params):
        if action == "dream":
            desc = params.get("description", "")
            num = params.get("num", 1)
            mode = params.get("mode", "combo")
            parts = [f"dream: {desc[:40]}" if desc else f"dream", f"{num}张", mode]
            if params.get("max_tags"):
                parts.append(f"tags<={params['max_tags']}")
            loras_p = params.get("loras")
            if loras_p:
                names = [l.get("name", l) if isinstance(l, dict) else l for l in loras_p[:3]]
                parts.append(f"lora:{','.join(names)}")
            return "  ".join(parts)
        if action == "run":
            mode = params.get("mode", self.mode)
            num = params.get("num", self.cli_args.get("num", 10))
            parts = [f"run: {num}张", mode]
            loras_p = params.get("loras")
            if loras_p:
                names = [l.get("name", l) if isinstance(l, dict) else l for l in loras_p[:3]]
                parts.append(f"lora:{','.join(names)}")
            return "  ".join(parts)
        if action == "gallery":
            run = params.get("run")
            return f"gallery: {run}" if run else "gallery: 最新批次"
        if action == "add_provider":
            masked = self.safety.mask_sensitive(action, params)
            url = masked.get("base_url", params.get("base_url", "?"))
            key_hint = ""
            if params.get("api_key"):
                key_hint = f" key={masked.get('api_key', '***')}"
            return f"add_provider: {url}{key_hint} caps={masked.get('capabilities', ['chat'])}"
        if action == "clear":
            return f"clear: {params.get('target', 'history')}"
        if action == "webui":
            return f"webui: 端口 {params.get('port', 7861)}"
        if action == "status":
            return "查看状态"
        if action == "history":
            return f"history: 最近 {params.get('last', 20)} 条"
        if action == "loras":
            if params.get("lora_action") == "triggers":
                return "loras: 列出触发词"
            if params.get("lora_action") == "set-trigger":
                return f"loras: 设置 {params.get('name')} 触发词"
            return "loras: 列表"
        if action == "artists":
            search = params.get("search", "")
            return f"artists: 搜索 {search}" if search else "artists: 列表"
        if action == "memory_set":
            return f"💾 记住: {params.get('key', '?')}={str(params.get('value', ''))[:40]}"
        if action == "memory_get":
            return f"🔍 回忆: {params.get('key', '?')}"
        if action == "memory_forget":
            return f"🗑 删除记忆: {params.get('key', '?')}"
        if action == "memory_list":
            return "📋 列出所有记忆"
        return action

    def _confirm_chain(self, chain):
        if len(chain) == 0:
            return True
        if self.tui:
            return self._confirm_chain_tui(chain)
        highest = self.safety.highest_risk(chain)
        if len(chain) == 1:
            step = chain[0]
            params = step.get("params", {})
            desc = self._step_desc(step["action"], params)
            risk = self.safety.risk_for(step["action"], params)
            risk_label = self.safety.label_for(risk)
            phrase = self.safety.confirm_phrase_for(step["action"], params)
            if phrase:
                print(f"  >> [{risk_label}] {desc}")
                print(f"     {self.safety.format_step(step['action'], params)}")
                cmd = input(f"  >> 请输入“{phrase}”执行，n=取消: ").strip().lower()
            else:
                cmd = input(f"  >> [{risk_label}] {desc}  [Enter=执行  n=取消  e=修改] ").strip().lower()
        else:
            print(f"  >> [链式 {len(chain)} 步 / 最高风险: {self.safety.label_for(highest)}]")
            for i, s in enumerate(chain):
                params = s.get("params", {})
                risk = self.safety.risk_for(s["action"], params)
                print(f"      {i+1}. [{self.safety.label_for(risk)}] {self._step_desc(s['action'], params)}")
                if risk in ("write", "destructive"):
                    print(f"         {self.safety.format_step(s['action'], params)}")
            phrase = "确认删除" if highest == "destructive" else "确认执行" if highest == "write" else ""
            if phrase:
                cmd = input(f"  >> 请输入“{phrase}”执行全部，n=取消: ").strip().lower()
            else:
                cmd = input(f"  >> [Enter=执行全部  n=取消] ").strip().lower()
        if cmd == "n":
            print("  已取消")
            return False
        if len(chain) == 1:
            step = chain[0]
            if self.safety.validate_confirmation(step["action"], step.get("params", {}), cmd):
                return True
        elif highest == "destructive":
            if cmd in ("确认删除", "delete", "yes delete"):
                return True
        elif highest == "write":
            if cmd in ("确认", "执行", "确认执行", "yes", "y"):
                return True
        elif cmd in ("", "y", "yes", "确认", "执行"):
            return True
        if cmd == "edit" and len(chain) == 1:
            new_desc = input("  新描述: ").strip()
            if new_desc:
                result = self._agent_process(new_desc)
                new_chain = self._extract_chain(result)
                if new_chain:
                    chain[:] = new_chain
                return self._confirm_chain(chain)
        print("  已取消")
        return False

    def _confirm_chain_tui(self, chain):
        """TUI 版本的 chain 确认."""
        steps = []
        for s in chain:
            params = s.get("params", {})
            desc = self._step_desc(s["action"], params)
            risk = self.safety.risk_for(s["action"], params)
            steps.append((s["action"], desc, risk))

        highest = self.safety.highest_risk(chain)
        phrase = ""
        risk_str = "info"
        if highest == "destructive":
            phrase = "确认删除"
            risk_str = "high"
        elif highest == "write":
            phrase = "确认执行"
            risk_str = "medium"

        result = self.tui.confirm(steps, phrase, risk_str)
        if result == "yes":
            return True
        elif result == "edit" and len(chain) == 1:
            self.tui.system("请输入新描述:", "info")
            new_desc = self.tui.ask("新描述")
            if new_desc:
                result = self._agent_process(new_desc)
                new_chain = self._extract_chain(result)
                if new_chain:
                    chain[:] = new_chain
                return self._confirm_chain(chain)
        self.tui.system("已取消", "info")
        return False

    def _extract_chain(self, result):
        if not isinstance(result, dict):
            return []
        chain = result.get("chain", [])
        if not chain and result.get("action") and result.get("action") != "chat":
            chain = [{"action": result["action"], "params": result.get("params", {})}]
        return chain

    def _execute_chain_steps(self, chain):
        failed = False

        def on_step(i, total, step):
            if i > 1:
                self._tui_or_print(f"链式: 第 {i}/{total} 步", "info")

        def on_error(step, action_result):
            nonlocal failed
            failed = True
            self._tui_or_print(f"{step['action']} 执行失败: {action_result.get('error')}", "err")

        def should_stop(step, action_result, index, total):
            return total == 1 and self._should_end_after_single_tool(step, action_result)

        self.chain_runner.run(chain, on_step=on_step, on_error=on_error, should_stop=should_stop)
        return not failed

    def _handle_choices(self, result):
        choices = result.get("choices") or []
        if not choices:
            return False
        reply = result.get("reply") or "我理解你可能有几种处理方式："
        if self.tui:
            self.tui.chat(reply)
        else:
            print(f"\n  {reply}")
        for i, choice in enumerate(choices, 1):
            label = choice.get("label") or f"选项 {i}"
            desc = choice.get("description") or ""
            print(f"  {i}. {label}" + (f" — {desc}" if desc else ""))
        try:
            _, session = self._session_current()
            self.agent.state.save_choices(session, "", choices)
            self._save_sessions()
        except Exception:
            pass
        try:
            ans = self.tui.ask("选择") if self.tui else input("  选择 [1-5 / n取消]: ").strip()
        except (EOFError, KeyboardInterrupt):
            self._tui_or_print("已取消", "info")
            return True
        if ans.lower() in ("n", "no", "q", "quit", "取消"):
            _, session = self._session_current()
            self.agent.state.mark_choice(session, cancelled=True)
            self._save_sessions()
            self._tui_or_print("已取消", "info")
            return True
        try:
            idx = int(ans) - 1
        except ValueError:
            self._tui_or_print("无效选择", "warn")
            return True
        if idx < 0 or idx >= len(choices):
            self._tui_or_print("无效选择", "warn")
            return True
        chain = choices[idx].get("chain") or []
        if not chain:
            self._tui_or_print("该选项没有可执行步骤", "warn")
            return True
        try:
            _, session = self._session_current()
            self.agent.state.mark_choice(session, index=idx, cancelled=False)
            self._save_sessions()
        except Exception:
            pass
        if not self._confirm_chain(chain):
            return True
        self._execute_chain_steps(chain)
        return True

    def _should_end_after_single_tool(self, step, action_result):
        """单步信息类工具已经直接把结果打印给用户，不需要再让 LLM 总结。"""
        if not action_result.get("ok", True):
            return False
        schema = self.tool_registry.schema(step.get("action"))
        if schema and schema.get("terminal"):
            return True
        return False

    def _should_end_after_chain(self, chain):
        """全只读链执行完结果已打印，直接结束，避免无价值 LLM 收尾。"""
        if all(self._should_end_after_single_tool(s, {"ok": True}) for s in chain):
            return True
        return self.safety.highest_risk(chain) == "read"

    # ── ReAct agent loop ──────────────────────────────────────────

    REACT_MAX_ITERATIONS = 100
    REACT_TOTAL_TIMEOUT = 1800  # 30 min
    REACT_LOOP_DETECT = 3       # 连续 3 轮相同响应则中止

    def _consume_pending_skill(self):
        """
        在非 _react_loop 上下文 (Telegram/WebUI) 中消费 _pending_skill_chain.
        skill_load 排队后, 没有 _react_loop 来消费, 需要直接执行.
        """
        chain = getattr(self, "_pending_skill_chain", None)
        body = getattr(self, "_pending_skill_body", "")
        self._pending_skill_chain = []
        self._pending_skill_body = ""
        if not chain:
            return
        for step in chain:
            if body and step.get("action") == "dream":
                desc = step.get("params", {}).get("description", "")
                if desc:
                    step.setdefault("params", {})["description"] = (
                        f"[Skill 上下文]\n{body}\n[用户描述]\n{desc}"
                    )
                else:
                    step.setdefault("params", {})["description"] = (
                        f"[Skill 上下文]\n{body}"
                    )
            try:
                self._execute_action(step["action"], step.get("params", {}))
            except Exception as e:
                print(f"  [ERR] skill chain step 执行失败: {e}")

    def _inject_action_result(self, step, action_result, persist_conversation=None):
        """把 action 执行结果回写到 conversation 和 tool_history, 供 LLM 下次决策参考."""
        sid, session = self._session_current()
        if persist_conversation is None:
            persist_conversation = bool(getattr(self, "_in_react_loop", False))
        output_lines = action_result.get("output", [])
        tail = output_lines[-30:] if len(output_lines) > 30 else output_lines
        output = "\n".join(tail)
        if action_result.get("error"):
            status = f"FAILED: {action_result['error']}"
        elif action_result.get("ok"):
            status = "OK"
        else:
            status = "UNKNOWN"
        msg = (
            f"[Tool Result: {step['action']}]\n"
            f"Status: {status}\n"
            f"Summary: {action_result.get('summary', '')}\n"
            f"Output (last {len(tail)} lines):\n{output}\n"
            "---\n"
            "判断任务是否完成。完成返回 {\"action\": \"chat\", \"reply\": \"...\"}；\n"
            "未完成则 chain 下一个动作继续执行。"
        )
        if persist_conversation:
            conv = session["conversation"]
            conv.append({"role": "user", "content": msg[:2000]})
            conv.append({
                "role": "assistant",
                "content": json.dumps({
                    "reply": f"已执行 {step['action']}，请决定下一步",
                    "action": "chat",
                    "params": {},
                }, ensure_ascii=False),
            })
        try:
            self.agent.state.save_tool_result(session, step, action_result)
        except Exception:
            pass
        self._save_sessions()

    def _react_loop(self, initial_input):
        """ReAct 风格主循环: LLM 决策 → 执行 → 结果回写 → 再次决策 → ... → 终止."""
        import time as _time
        self._in_react_loop = True
        start = _time.time()
        last_responses = []
        # 清空 skill_load 留下的 pending chain (避免上次的残留影响本次)
        self._pending_skill_chain = []

        for iteration in range(self.REACT_MAX_ITERATIONS):
            # 处理上轮 skill_load 留下的 pending chain (优先于 LLM 决策)
            if getattr(self, "_pending_skill_chain", None):
                chain = self._pending_skill_chain
                self._pending_skill_chain = []
                skill_body = getattr(self, "_pending_skill_body", "")
                self._pending_skill_body = ""
                self._tui_or_print(f"skill-chain 接管 {len(chain)} 步", "info")
                # 注入 skill body 到 dream 的 description (LLM 能看到 skill 约束)
                if skill_body:
                    for step in chain:
                        if step.get("action") == "dream":
                            desc = step.get("params", {}).get("description", "")
                            if desc:
                                step.setdefault("params", {})["description"] = (
                                    f"[Skill 上下文]\n{skill_body}\n"
                                    f"[用户描述]\n{desc}"
                                )
                            else:
                                step.setdefault("params", {})["description"] = (
                                    f"[Skill 上下文]\n{skill_body}"
                                )
                if not self._confirm_chain(chain):
                    return
                for i, step in enumerate(chain, 1):
                    if i > 1:
                        self._tui_or_print(f"链式: 第 {i}/{len(chain)} 步", "info")
                    action_result = self._execute_action(step["action"], step.get("params", {}))
                    self._inject_action_result(step, action_result)
                    if not action_result.get("ok", True):
                        self._tui_or_print(f"{step['action']} 执行失败: {action_result.get('error')}", "err")
                        return
                if self._should_end_after_chain(chain):
                    return
                continue  # 继续下一轮, 让 LLM 看结果
            if _time.time() - start > self.REACT_TOTAL_TIMEOUT:
                self._tui_or_print(f"总耗时超过 {self.REACT_TOTAL_TIMEOUT}s, 中止", "warn")
                return

            if iteration == 0:
                user_msg = initial_input
            else:
                user_msg = "继续。基于上一步的工具输出决定下一步。"

            try:
                result = self._agent_process(
                    user_msg,
                    source="tool_result" if iteration > 0 else "cli",
                )
            except Exception as e:
                if iteration == 0 and self._handle_llm_failure(e):
                    try:
                        result = self._agent_process(user_msg, source="cli")
                    except Exception as retry_error:
                        self._tui_or_print(f"LLM 调用失败: {retry_error}", "err")
                        return
                else:
                    self._tui_or_print(f"LLM 调用失败: {e}", "err")
                    return

            if not isinstance(result, dict):
                self._tui_or_print(f"LLM 返回格式异常: {result}", "err")
                return

            reply = result.get("reply", "")
            if self._handle_choices(result):
                return
            chain = self._extract_chain(result)

            if not chain:
                if reply:
                    if self.tui:
                        self.tui.chat(reply)
                    else:
                        print(f"\n  {reply}")
                else:
                    self._tui_or_print("LLM 未返回 chain, 结束", "info")
                return

            sig = json.dumps(chain, sort_keys=True, ensure_ascii=False)
            last_responses.append(sig)
            if len(last_responses) > self.REACT_LOOP_DETECT:
                last_responses.pop(0)
            if len(last_responses) == self.REACT_LOOP_DETECT and len(set(last_responses)) == 1:
                self._tui_or_print(f"连续 {self.REACT_LOOP_DETECT} 轮相同响应, 强制中止", "warn")
                self._tui_or_print("LLM 似乎卡在同一个任务, 请用更明确指令重新发起", "warn")
                return

            auto_research = self._is_generation_research_chain(chain)
            if not auto_research and not self._confirm_chain(chain):
                return

            for i, step in enumerate(chain, 1):
                if i > 1:
                    self._tui_or_print(f"链式: 第 {i}/{len(chain)} 步", "info")
                action_result = self._execute_action(step["action"], step.get("params", {}))
                self._inject_action_result(step, action_result)
                if not action_result.get("ok", True):
                    self._tui_or_print(f"{step['action']} 执行失败: {action_result.get('error')}", "err")
                    return
                if len(chain) == 1 and not auto_research and self._should_end_after_single_tool(step, action_result):
                    return
                # 如果该步是 skill_load 且排出了 pending chain:
                # - 把 skill 的 loras 注入到 LLM 剩余 dream 步骤中
                # - 替换 pending chain 为 LLM 剩余步骤 (让 pending handler 注入 skill body)
                # - 跳出 (避免重复执行, 下一轮 pending handler 处理)
                if step["action"] == "skill_load" and getattr(self, "_pending_skill_chain", None):
                    pending = self._pending_skill_chain
                    self._pending_skill_chain = []  # 先清空
                    remaining = chain[i:]
                    skill_loras = None
                    for ps in pending:
                        if ps.get("action") == "dream" and ps.get("params", {}).get("loras"):
                            skill_loras = ps["params"]["loras"]
                            break
                    if skill_loras:
                        for rs in remaining:
                            if rs.get("action") == "dream":
                                existing = rs.setdefault("params", {}).setdefault("loras", [])
                                for sl in skill_loras:
                                    if sl not in existing:
                                        existing.insert(0, sl)
                    self._pending_skill_chain = remaining
                    self._tui_or_print(f"skill-chain 注入 loras 到 {len(remaining)} 步, 保留 LLM chain 步骤数", "info")
                    break
            if not auto_research and self._should_end_after_chain(chain):
                return

    def _is_generation_research_chain(self, chain):
        if len(chain or []) != 1:
            return False
        if chain[0].get("action") not in ("character_resolve", "tagsite", "tags"):
            return False
        _, session = self._session_current()
        task = (session.get("conversation_state") or {}).get("active_task") or {}
        return task.get("type") == "generation" and task.get("status") == "researching"

    def _to_int(self, v, default):
        if v is None:
            return default
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    def _to_float(self, v, default):
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _execute_action(self, action, params):
        return self.tool_executor.execute(action, params)

    def _execute_action_raw(self, action, params):
        raise RuntimeError(f"未注册工具: {action}")

    def _handle_slash_command(self, line):
        parts = line[1:].split()
        cmd = parts[0].lower() if parts else ""

        if cmd == "new":
            self._session_create()
            _, s = self._session_current()
            if self.tui:
                self.tui.system(f"新对话: {s['name']}", "ok")
                self.tui.update_header(session=s["name"])
            else:
                print(f"  [OK] 新对话: {s['name']}")
        elif cmd in ("switch", "s"):
            if len(parts) > 1:
                if self._session_switch(parts[1]):
                    _, s = self._session_current()
                    self._print_banner(s)
                else:
                    self._tui_or_print("未找到对话", "err")
            else:
                self._tui_or_print("用法: /switch <编号或ID>", "warn")
        elif cmd == "rename":
            if len(parts) > 1:
                sid, _ = self._session_current()
                self._session_rename(sid, " ".join(parts[1:]))
                _, s = self._session_current()
                self._print_banner(s)
            else:
                self._tui_or_print("用法: /rename <新名称>", "warn")
        elif cmd == "delete":
            sid, s = self._session_current()
            name = s["name"]
            self._session_delete(sid)
            if self.tui:
                self.tui.system(f"已删除: {name}", "ok")
            else:
                print(f"  [OK] 已删除: {name}")
            _, new_s = self._session_current()
            if new_s:
                self._print_banner(new_s)
        elif cmd in ("sessions", "ls", "list"):
            text = self._session_list_text()
            if self.tui:
                self.tui.system(text, "info")
                self.tui.system("切换对话: /switch <编号>", "info")
            else:
                print(text)
        elif cmd in ("help", "h"):
            help_text = (
                "  命令指南:\n"
                "  /new              - 新对话\n"
                "  /list             - 列出对话\n"
                "  /switch <编号>    - 切换对话\n"
                "  /rename <名称>    - 重命名当前对话\n"
                "  /delete           - 删除当前对话\n"
                "  /critique [路径/last] [预期描述] - 看图分析\n"
                "  /models status|list|test|switch <chat/vision> <key> - 管理模型\n"
                "  /provider <base_url> <api_key> [name] - 添加模型服务商\n"
                "  /tagsite <角色名>  - 查角色Prompt tags\n"
                "  /skill             - skill 编写指导\n"
                "  /telegram [start/stop/status] - 管理Telegram机器人\n"
                "  /help             - 本帮助\n"
                "  /exit             - 退出\n"
                "  -------------------\n"
                "  直接说自然语言，苏丹娜会自动理解你的意图"
            )
            if self.tui:
                self.tui.system(help_text, "info")
            else:
                print(help_text)
        elif cmd == "tag" or cmd == "tagsite":
            if len(parts) > 1:
                self._run_cmd(self.cmd_tagsite, *parts[1:])
            else:
                self._tui_or_print("用法: /tagsite <角色名>", "warn")
        elif cmd == "skill":
            self._run_cmd(self.cmd_skill_create, show_help=True)
        elif cmd == "telegram":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            self._run_cmd(self.cmd_telegram, action=sub)
        elif cmd == "models":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            if sub == "status":
                self._run_cmd(self.cmd_models, "status")
            elif sub == "list":
                self._run_cmd(self.cmd_models, "list")
            elif sub == "test":
                self._run_cmd(self.cmd_models, "test", model_key=parts[2] if len(parts) > 2 else None)
            elif sub == "switch" and len(parts) >= 4:
                self._run_cmd(self.cmd_models, "switch", role=parts[2], model_key=parts[3])
            else:
                self._tui_or_print(
                    "用法: /models list           — 列出所有模型\n"
                    "         /models status         — 查看当前模型\n"
                    "         /models test [key]      — 测试模型是否能回复\n"
                    "         /models switch chat <key>  — 切换对话模型\n"
                    "         /models switch vision <key> — 切换识图模型",
                    "warn",
                )
        elif cmd == "provider":
            if len(parts) < 3:
                self._tui_or_print("用法: /provider <base_url> <api_key> [name] [chat|chat,vision] [switch_role]", "warn")
            else:
                self._run_cmd(
                    self.cmd_add_provider,
                    base_url=parts[1],
                    api_key=parts[2],
                    provider_name=parts[3] if len(parts) > 3 else None,
                    capabilities=parts[4] if len(parts) > 4 else None,
                    switch_role=parts[5] if len(parts) > 5 else None,
                )
        elif cmd == "critique":
            rest = " ".join(parts[1:]) if len(parts) > 1 else ""
            path = "last"
            expected = None
            if rest:
                if rest.startswith("last "):
                    expected = rest[5:].strip()
                elif rest == "last":
                    pass
                else:
                    path = rest
            self._run_cmd(self.cmd_critique, path=path, expected=expected)
        elif cmd in ("exit", "quit", "q"):
            if self.tui:
                self.tui.stop()
            print("Bye.")
            sys.exit(0)
        else:
            self._tui_or_print(f"未知命令: /{cmd}  输入 /help 查看可用命令", "warn")

    def _handle_llm_failure(self, error):
        key = self._get_selection().get("chat")
        kind = self._model_error_kind(error)
        if kind == "not_found":
            if key:
                self._mark_model_status(key, False, error)
            self._tui_or_print(f"当前模型不可用: {self._model_error_text(error)[:160]}", "warn")
            self._chat_model = None
            try:
                self._chat_model = self._fallback_model_client("chat", {key} if key else set(), reason=error)
                self._update_tui_header()
                return True
            except Exception as fb_error:
                self._tui_or_print(str(fb_error), "err")
                return False
        if kind == "empty_content":
            self._tui_or_print(
                f"当前 chat 模型返回空内容，不会标记为模型不可用: {self._model_error_text(error)[:180]}",
                "warn",
            )
            self._chat_model = None
            return False
        return False

    def _print_banner(self, session, usage=""):
        name = session["name"]
        w = 53
        print()
        print("  " + "+" + "-" * w + "+")
        print("  " + "|" + "       [ 苏 丹 娜 ]  AI 助 手" + " " * (w - 27) + "|")
        print("  " + "|" + "    自然语言  >  Stable Diffusion 绘图" + " " * (w - 35) + "|")
        print("  " + "+" + "-" * w + "+")
        print(f"  -- 对话: {name}" + (f"    {usage}" if usage else ""))

    def _tui_or_print(self, msg, msg_type="info"):
        if self.tui:
            self.tui.system(msg, msg_type)
        else:
            icons = {"ok": "[OK]", "err": "[ERR]", "warn": "[WARN]", "info": "[INFO]"}
            print(f"  {icons.get(msg_type, '[INFO]')} {msg}")

    def _update_tui_header(self):
        if not self.tui:
            return
        sid, session = self._session_current()
        self.tui.update_header(
            model=self._chat_model.model if self._chat_model else self._selection_model_name("chat"),
            session=session["name"],
        )

    def _run_cmd(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def _status_bar(self):
        if self._chat_model is None:
            return ""
        try:
            return self._chat_model.get_usage_str()
        except Exception:
            return ""

    def cmd_agent(self):
        if not self.sessions["sessions"]:
            self._session_create("默认对话")
        sid, session = self._session_current()

        self.tui = TUIController()
        try:
            self._update_tui_header()
            self.tui.start()
        except Exception:
            if self.tui:
                self.tui.stop()
            self.tui = None
            self._print_banner(session)

        while True:
            try:
                if self.tui:
                    line = self.tui.ask("input")
                else:
                    prompt = f"\nYou [{self._status_bar()}]: " if self._chat_model else "\nYou: "
                    line = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                if self.tui:
                    self.tui.stop()
                print("  Su Dan Na is waiting for you~")
                self.tui = None
                self._save_sessions()
                self._cleanup_telegram()
                break

            if not line:
                continue
            if line in ("quit", "exit", "q"):
                if self.tui:
                    self.tui.stop()
                print("  Su Dan Na is waiting for you~")
                self.tui = None
                self._save_sessions()
                self._cleanup_telegram()
                break

            if line.startswith("/"):
                self._handle_slash_command(line)
                continue

            try:
                self._react_loop(line)
            except KeyboardInterrupt:
                if self.tui:
                    self.tui.system("User pressed Ctrl+C, stopped current task", "warn")
                else:
                    print("\n  [Interrupt] Stopped current task")
                continue
            except Exception as e:
                if self._handle_llm_failure(e):
                    try:
                        self._react_loop(line)
                    except Exception as retry_error:
                        self._tui_or_print(f"LLM 调用失败: {retry_error}", "err")
                    continue
                if self.tui:
                    self.tui.system(f"{e}", "err")
                else:
                    print(f"\n  [ERR] {e}")
                if "LLM" in str(e) or "Connection" in str(e) or "connect" in str(e).lower():
                    msg = "LLM connection failed, please check LLM service"
                    if self.tui:
                        self.tui.system(msg, "err")
                    else:
                        print(f"  {msg}")
                    self._save_sessions()
                    break

    cmd_shell = cmd_agent

    # ── dispatch ───────────────────────────────────────────────────

    def _dispatch(self, args):
        return dispatch(self, args)


# ── CLI ───────────────────────────────────────────────────────────


def main():
    args = parse_args()
    if not args.command:
        args.command = "shell"
    try:
        tester = SDArtistTester()
        if args.command == "shell" and tester.config.get("telegram", {}).get("auto_start", False):
            token = tester.config.get("telegram", {}).get("token", "")
            if token:
                tester.cmd_telegram(action="start")
        tester._dispatch(args)
    except KeyboardInterrupt:
        print("\n  已停止")
        sys.exit(130)
    except Exception as e:
        print(f"  [ERR] {type(e).__name__}: {e}", file=sys.stderr)
        if getattr(args, "debug", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
