import json


ACTION_RISK = {
    "status": "read",
    "models": "read",
    "loras": "read",
    "artists": "read",
    "history": "read",
    "gallery": "read",
    "tagsite": "read",
    "tags": "read",
    "skill_list": "read",
    "session_list": "read",
    "llm_status": "read",
    "config_get": "read",

    "dream": "generation",
    "run": "generation",
    "critique": "generation",

    "web_fetch": "network",
    "webui": "network",
    "telegram": "network",
    "add_provider": "network",
    "llm_test": "network",

    "config_set": "write",
    "file_write": "write",
    "skill_create": "write",
    "skill_load": "low",
    "session_new": "low",
    "session_switch": "low",
    "session_rename": "low",

    "memory_get": "read",
    "memory_list": "read",

    "memory_set": "low",

    "clear": "destructive",
    "file_delete": "destructive",
    "memory_forget": "destructive",
}

RISK_ORDER = {
    "read": 0,
    "low": 1,
    "generation": 2,
    "network": 3,
    "write": 4,
    "destructive": 5,
}

RISK_LABEL = {
    "read": "只读",
    "low": "低风险",
    "generation": "生成",
    "network": "联网/服务",
    "write": "写入",
    "destructive": "高危",
}


class SafetyPolicy:
    def risk_for(self, action, params=None):
        return ACTION_RISK.get(action, "write")

    def highest_risk(self, chain):
        risk = "read"
        for step in chain or []:
            step_risk = self.risk_for(step.get("action"), step.get("params") or {})
            if RISK_ORDER[step_risk] > RISK_ORDER[risk]:
                risk = step_risk
        return risk

    def label_for(self, risk):
        return RISK_LABEL.get(risk, risk)

    def confirm_phrase_for(self, action, params=None):
        risk = self.risk_for(action, params)
        if risk == "destructive":
            return "确认删除"
        if risk == "write":
            return "确认执行"
        return ""

    def validate_confirmation(self, action, params, user_input):
        risk = self.risk_for(action, params)
        text = (user_input or "").strip().lower()
        if risk in ("read", "low", "generation"):
            return text in ("", "y", "yes", "确认", "执行")
        if risk == "network":
            return text in ("", "y", "yes", "确认", "执行")
        if risk == "write":
            return text in ("确认", "执行", "确认执行", "yes", "y")
        if risk == "destructive":
            return text in ("确认删除", "delete", "yes delete")
        return False

    def format_step(self, action, params=None):
        params = params or {}
        params = self.mask_sensitive(action, params)
        risk = self.risk_for(action, params)
        label = self.label_for(risk)
        return f"[{label}] {action} params={json.dumps(params, ensure_ascii=False)}"

    def mask_sensitive(self, action, params=None):
        params = dict(params or {})
        from .schemas import TOOL_SCHEMAS
        schema = TOOL_SCHEMAS.get(action, {})
        sensitive = schema.get("sensitive_params") or []
        for key in sensitive:
            if params.get(key):
                val = str(params[key])
                params[key] = val[:8] + "..." + val[-4:] if len(val) > 14 else "***"
        return params

    def is_destructive(self, action, params=None):
        return self.risk_for(action, params) == "destructive"

    def is_safe_clear_target(self, target):
        return target in ("history", "outputs")
