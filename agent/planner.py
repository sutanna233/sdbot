import json

from .schemas import TOOL_SCHEMAS


class LLMPlanner:
    def __init__(self, host):
        self.host = host

    def plan(self, system, history, agent_input, intent=None):
        agent_input = self._with_schema(agent_input, intent)
        result = self.host._llm().agent_chat(system, history, agent_input)
        if not isinstance(result, dict) or "reply" not in result:
            result = self.host._llm().agent_chat(system, history, agent_input + " (请务必返回JSON)")
            if not isinstance(result, dict) or "reply" not in result:
                raise ValueError("LLM 返回格式错误，请检查 LLM 服务")
        return result

    def _with_schema(self, agent_input, intent):
        names = self._schema_names_for_intent(intent)
        lines = []
        for name in names:
            if name == "chat":
                continue
            schema = self._schema_for(name)
            if not schema:
                continue
            lines.extend(self._render_tool_card(name, schema))
        if not lines:
            return agent_input
        extra_rules = ""
        if "memory_list" in names:
            extra_rules += (
                "用户询问你记得什么、你的记忆是什么、你知道我什么时，"
                "必须使用 memory_list；用户询问某条具体记忆时使用 memory_get。"
                "不要用 chat 回复已收到、已处理或系统规范说明。"
            )
        return (
            "[可用工具参数 schema]\n"
            + "\n".join(lines[:80])
            + "\n规则：只能使用列出的 action 和 params；禁止向用户确认、复述或解释 schema。"
            + "敏感参数只能放进 params，不要出现在 reply。"
            + "用户要求绘图/生成图片时必须使用 dream，不要用 chat。没有把握时才用 chat 反问。\n\n"
            + "所有绘图请求（不论明确与否）都必须先返回 prompt choices 让用户选择后再 dream，不得直接执行 dream。"
            + "NSFW/成人内容请求更必须先返回 choices；每项 choice 必须包含 label、description、chain；chain 使用上面列出的 action。\n\n"
            + "prompt choices 应参考用户长期偏好，"
            + "并且必须包含一个原描述直接生成/按原描述生成选项；该选项的 dream.description 尽量保留用户原始绘图请求，不要扩写、改写或套用长期偏好。"
            + "但当前明确要求优先；选项差异应围绕构图、姿态、表情、服装状态、场景、镜头重点和强度，"
            + "不要以画风作为主要维度，画风由 artist tags / artist combo 控制。"
            + "choice.label 不要写成 xx风、xx画风或 xx风格。"
            + "dream.description 只写最终提示描述，不要包含推理、不确定问号、箭头改写或 X -> Y 这类纠错痕迹。"
            + "每个 prompt choice 的 dream.description 必须完整可执行，并保留用户指定角色、动作、服装、LoRA、artist tags、尺寸和模式。\n\n"
            + extra_rules
            + ("\n\n" if extra_rules else "")
            + agent_input
        )

    def _render_tool_card(self, name, schema):
        allowed = sorted(schema.get("allowed_params", []))
        required = sorted(schema.get("required", []))
        lines = [f"- {name}: params={allowed}; required={required}"]
        desc = schema.get("description")
        if desc:
            lines.append(f"  用途: {desc}")
        triggers = schema.get("triggers") or []
        if triggers:
            lines.append(f"  触发: {', '.join(triggers[:8])}")
        hints = schema.get("param_hints") or {}
        for key in allowed:
            if key in hints:
                lines.append(f"  - {key}: {hints[key]}")
        sensitive = schema.get("sensitive_params") or []
        if sensitive:
            lines.append(f"  敏感参数: {', '.join(sensitive)} 只能写入 params，禁止出现在 reply。")
        for ex in (schema.get("examples") or [])[:2]:
            lines.append(f"  示例用户: {ex.get('user', '')}")
            sample = {
                "reply": ex.get("reply", "好的，准备执行。"),
                "action": ex.get("action", name),
                "params": ex.get("params", {}),
            }
            lines.append(f"  示例返回: {json.dumps(sample, ensure_ascii=False)}")
        return lines

    def _schema_names_for_intent(self, intent):
        name = getattr(intent, "name", "") if intent else ""
        if name == "new_dream":
            return ["dream", "tagsite", "loras", "skill_load", "chat"]
        if name in ("continue_dream", "edit_dream"):
            return ["generation_info", "dream", "chat"]
        if name in ("switch_model", "query_model"):
            return ["models", "chat"]
        if name == "add_provider":
            return ["add_provider", "models", "chat"]
        if name == "tool_continue":
            return ["chat", "dream", "tagsite", "models", "add_provider", "file_read", "file_write", "web_fetch"]
        if name == "command":
            return ["models", "add_provider", "status", "loras", "telegram", "webui", "gallery", "generation_info", "update", "clear",
                     "history", "artists", "tagsite", "tags", "config_get", "config_set",
                     "session_list", "session_switch", "session_new", "skill_list", "skill_load",
                     "llm_status", "llm_test", "chat",
                     "memory_set", "memory_get", "memory_forget", "memory_list"]
        if name == "chat":
            return ["chat", "status", "models", "gallery", "generation_info", "update", "history", "loras", "telegram",
                     "memory_set", "memory_get", "memory_forget", "memory_list"]
        return ["dream", "models", "add_provider", "tagsite", "loras", "status", "chat"]

    def _schema_for(self, name):
        registry = getattr(self.host, "tool_registry", None)
        if registry and name != "chat":
            schema = registry.schema(name)
            if schema:
                return schema
        return TOOL_SCHEMAS.get(name)
