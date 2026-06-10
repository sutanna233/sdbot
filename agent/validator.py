from .schemas import TOOL_SCHEMAS


class ActionValidator:
    def __init__(self, host):
        self.host = host

    def validate_and_repair(self, intent, user_input, result, ctx):
        if not isinstance(result, dict):
            return result
        action = result.get("action")
        if result.get("choices"):
            clean_choices = []
            for choice in result.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                clean = {
                    "label": str(choice.get("label") or "选项")[:40],
                    "description": str(choice.get("description") or "")[:200],
                }
                chain = choice.get("chain") or []
                if not chain and choice.get("action"):
                    chain = [{"action": choice.get("action"), "params": choice.get("params", {})}]
                clean_chain = []
                for step in chain:
                    if not isinstance(step, dict) or not self._is_registered(step.get("action")):
                        continue
                    self._sanitize_step(step)
                    self._normalize_step(step)
                    self._coerce_types(step)
                    self._check_param_types(step)
                    err = self._check_required(step)
                    if err:
                        clean_chain = []
                        break
                    clean_chain.append(step)
                if clean_chain:
                    clean["chain"] = clean_chain
                    clean_choices.append(clean)
            if clean_choices:
                result["choices"] = clean_choices[:5]
                result["action"] = "chat"
                result["params"] = {}
                return result
            result.pop("choices", None)
        if result.get("chain"):
            clean_chain = []
            for step in result.get("chain") or []:
                if not self._is_registered(step.get("action")):
                    continue
                self._sanitize_step(step)
                self._normalize_step(step)
                self._coerce_types(step)
                self._check_param_types(step)
                err = self._check_required(step)
                if err:
                    return err
                clean_chain.append(step)
            result["chain"] = clean_chain
            if not clean_chain:
                return {"reply": "没有可执行的有效工具步骤，请重新说明你的需求。", "action": "chat", "params": {}}
            return result
        if action:
            if not self._is_registered(action) and action != "chat":
                return {"reply": f"未知操作: {action}，请重新说明你的需求。", "action": "chat", "params": {}}
            self._sanitize_step(result)
            self._normalize_step(result)
            self._coerce_types(result)
            self._check_param_types(result)
            err = self._check_required(result)
            if err:
                return err
        return result

    def _is_registered(self, action):
        if action == "chat":
            return True
        registry = getattr(self.host, "tool_registry", None)
        if not registry:
            return action in TOOL_SCHEMAS
        return action in registry.names()

    def _schema_for(self, action):
        registry = getattr(self.host, "tool_registry", None)
        if registry and action in registry.names():
            sch = registry.schema(action)
            if sch:
                return sch
        return TOOL_SCHEMAS.get(action)

    _ALIASES = {
        "file_read": {"path": {"file_path", "target", "file", "filename"}},
        "file_write": {"path": {"file_path", "target", "file", "filename"}},
        "file_delete": {"path": {"file_path", "target", "file", "filename"}},
        "file_list": {"path": {"dir", "directory", "target", "folder"}},
        "web_fetch": {"url": {"uri", "link", "endpoint", "target_url"}},
        "add_provider": {"base_url": {"url", "endpoint"}, "api_key": {"key", "token"}, "capabilities": {"capability"}},
    }

    def _sanitize_step(self, step):
        action = step.get("action")
        schema = self._schema_for(action)
        if not schema:
            return
        params = step.get("params") or {}
        allowed = set(schema.get("allowed_params", set()))
        for canonical, aliases in self._ALIASES.get(action, {}).items():
            allowed.add(canonical)
            allowed |= aliases
        step["params"] = {k: v for k, v in params.items() if k in allowed}

    def _normalize_step(self, step):
        action = step.get("action")
        params = step.setdefault("params", {})
        if action == "add_provider":
            if "url" in params and "base_url" not in params:
                params["base_url"] = params.pop("url")
            if "endpoint" in params and "base_url" not in params:
                params["base_url"] = params.pop("endpoint")
            if "key" in params and "api_key" not in params:
                params["api_key"] = params.pop("key")
            if "token" in params and "api_key" not in params:
                params["api_key"] = params.pop("token")
            if "capability" in params and "capabilities" not in params:
                params["capabilities"] = params.pop("capability")
            if not params.get("capabilities"):
                params["capabilities"] = ["chat"]
            if isinstance(params.get("capabilities"), str):
                caps = [c.strip() for c in params["capabilities"].replace("/", ",").replace("|", ",").split(",")]
                params["capabilities"] = [c for c in caps if c]
        if action in ("file_read", "file_write", "file_delete"):
            for alias in ("file_path", "target", "file", "filename"):
                if alias in params and "path" not in params:
                    params["path"] = params.pop(alias)
        if action == "file_list":
            for alias in ("dir", "directory", "target", "folder"):
                if alias in params and "path" not in params:
                    params["path"] = params.pop(alias)
        if action == "web_fetch":
            for alias in ("uri", "link", "endpoint", "target_url"):
                if alias in params and "url" not in params:
                    params["url"] = params.pop(alias)
        if action == "tagsite":
            names = params.get("names")
            if isinstance(names, str):
                params["names"] = [names]
            elif not isinstance(names, list):
                params["names"] = []
            else:
                params["names"] = [str(n).strip() for n in names if str(n).strip()]
                if not params["names"]:
                    params["names"] = []
        elif action == "models":
            sub = params.get("action") or "list"
            if sub == "switch":
                role = params.get("role")
                if role not in ("chat", "vision"):
                    params["role"] = "chat"
            params["action"] = sub
        elif action == "telegram":
            params["subaction"] = params.get("subaction") or "start"
        elif action == "web_fetch":
            params["format"] = params.get("format") or "text"
        elif action == "file_list":
            params["path"] = params.get("path") or "."
            params["max_count"] = params.get("max_count") or 100
        elif action == "file_find":
            params["pattern"] = params.get("pattern") or "*"
            params["max_count"] = params.get("max_count") or 20
        elif action == "history":
            params["last"] = params.get("last") or 20
        elif action == "loras":
            if not params.get("lora_action") and not params.get("search"):
                params["lora_action"] = "list"
        elif action == "skill_create":
            if isinstance(params.get("triggers"), str):
                params["triggers"] = [t.strip() for t in params["triggers"].split(",") if t.strip()]

    def _coerce_types(self, step):
        params = step.setdefault("params", {})
        int_keys = {
            "num", "max_tags", "steps", "seed", "width", "height", "min_artists", "max_artists",
            "last", "port", "max_count", "start_line", "max_lines", "max_length",
        }
        float_keys = {"cfg_scale"}
        bool_keys = {"confirm_yes", "count_only", "list_only", "regenerate", "open_after", "background"}
        for key in list(params.keys()):
            if key in int_keys and params[key] is not None:
                try:
                    params[key] = int(params[key])
                except (TypeError, ValueError):
                    params.pop(key, None)
            elif key in float_keys and params[key] is not None:
                try:
                    params[key] = float(params[key])
                except (TypeError, ValueError):
                    params.pop(key, None)
            elif key in bool_keys:
                if isinstance(params[key], str):
                    params[key] = params[key].strip().lower() in ("1", "true", "yes", "y", "是", "确认")
                else:
                    params[key] = bool(params[key])
        for key in ("names", "triggers"):
            v = params.get(key)
            if v is not None and not isinstance(v, list):
                if isinstance(v, str):
                    params[key] = [v]
                else:
                    params[key] = [v]
        loras = params.get("loras")
        if loras is not None and not isinstance(loras, list):
            params["loras"] = [loras]

    def _check_required(self, step):
        action = step.get("action")
        if action == "chat":
            return None
        schema = self._schema_for(action)
        if not schema:
            return None
        params = step.get("params") or {}
        for req in schema.get("required", []):
            v = params.get(req)
            if v is None or v == "" or v == [] or v == {}:
                return {
                    "reply": f"操作 {action} 缺少必要参数: {req}，请补充后重试。",
                    "action": "chat",
                    "params": {},
                }
        return None

    def _check_param_types(self, step):
        action = step.get("action")
        params = step.get("params") or {}
        if action == "dream":
            desc = params.get("description")
            if isinstance(desc, str) and not desc.strip():
                params.pop("description", None)
        elif action == "tagsite":
            names = params.get("names")
            if isinstance(names, list):
                params["names"] = [str(n) for n in names if str(n).strip()]
        elif action == "web_fetch":
            url = params.get("url")
            if isinstance(url, str):
                u = url.strip()
                if not (u.startswith("http://") or u.startswith("https://")):
                    params.pop("url", None)
        elif action == "add_provider":
            url = params.get("base_url")
            if isinstance(url, str):
                u = url.strip()
                if u.startswith("http://") or u.startswith("https://"):
                    params["base_url"] = u
                else:
                    params.pop("base_url", None)
            caps = params.get("capabilities")
            if isinstance(caps, list):
                params["capabilities"] = [c for c in caps if c in ("chat", "vision")]
            if not params.get("capabilities"):
                params["capabilities"] = ["chat"]
            if params.get("switch_role") not in (None, "chat", "vision"):
                params.pop("switch_role", None)
        elif action in ("file_read", "file_write", "file_delete"):
            path = params.get("path")
            if isinstance(path, str):
                p = path.strip()
                if p.startswith("/") or ".." in p.replace("\\", "/").split("/"):
                    params.pop("path", None)
        elif action == "models" and params.get("action") == "switch":
            role = params.get("role")
            if role not in ("chat", "vision"):
                params["role"] = "chat"
            model = params.get("model")
            if isinstance(model, str):
                model = model.strip()
                params["model"] = model
