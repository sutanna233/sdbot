import json

from .schemas import TOOL_SCHEMAS


class LLMPlanner:
    def __init__(self, host):
        self.host = host

    def plan(self, system, history, agent_input, intent=None, ctx=None):
        agent_input = self._with_schema(agent_input, intent, ctx=ctx)
        result = self.host._llm().agent_chat(system, history, agent_input)
        if not isinstance(result, dict) or "reply" not in result:
            result = self.host._llm().agent_chat(system, history, agent_input + " (请务必返回JSON)")
            if not isinstance(result, dict) or "reply" not in result:
                raise ValueError("LLM 返回格式错误，请检查 LLM 服务")
        return result

    def _with_schema(self, agent_input, intent, ctx=None):
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
        choice_rules = self._choice_rules(intent, ctx)
        return (
            "[可用工具参数 schema]\n"
            + "\n".join(lines[:80])
            + "\n规则：只能使用列出的 action 和 params；禁止向用户确认、复述或解释 schema。"
            + "敏感参数只能放进 params，不要出现在 reply。"
            + "明确绘图/生成图片请求必须使用 dream，不要用 chat。没有把握时才用 chat 反问。\n\n"
            + choice_rules
            + extra_rules
            + ("\n\n" if extra_rules else "")
            + agent_input
        )

    def _choice_rules(self, intent, ctx=None):
        name = getattr(intent, "name", "") if intent else ""
        state = (getattr(ctx, "data", {}) or {}).get("conversation_state") if ctx else {}
        active = (state or {}).get("active_task") or {}
        has_pending_choices = bool((state or {}).get("last_choices")) and active.get("status") == "waiting_choice"
        has_researched_generation = name == "tool_continue" and active.get("type") == "generation" and active.get("status") == "research_done"
        needs_character_confirmation = name == "tool_continue" and active.get("type") == "generation" and active.get("status") == "needs_character_confirmation"
        is_new_dream_op = name in ("new_dream", "edit_dream")
        is_pending_choice_followup = name == "contextual_followup" and has_pending_choices
        if not is_new_dream_op and not is_pending_choice_followup and not has_researched_generation and not needs_character_confirmation:
            return (
                "短疑问、解释、情绪、搜索追问、模型/配置/状态问题不得返回 prompt choices，"
                "也不得擅自执行 dream。\n\n"
            )
        research_rule = ""
        if name == "new_dream":
            research_rule = (
                "如果绘图请求包含具体角色、人名、作品名或专有名词（例如某游戏/动画角色），"
                "必须先返回单步 chain 调用 character_resolve 解析角色，不要直接调用 tagsite，不要直接返回 choices，也不要直接 dream。"
                "character_resolve.params.request 必须保留用户原文；characters 只放角色/人物名，works 只放作品/系列名。"
                "如果只是通用主体（猫娘、少女、风景等）或用户已提供完整标签，则可以直接返回 choices。\n\n"
            )
        if has_researched_generation:
            research_rule = (
                "当前已经完成角色/tag research。必须基于 conversation_state.last_tool_result 和 active_task.goal 生成 prompt choices，"
                "优先使用 last_tool_result.result.matches[].tags / result.tags 中的结构化标签；"
                "不要再次调用 tagsite，不要直接 dream；未查到时不得脑补角色服装或设定。\n\n"
            )
        if needs_character_confirmation:
            return (
                "角色解析没有高置信结果。必须先返回确认类 choices 或 chat 说明，不得返回构图/姿势类 prompt choices，不得直接 dream。"
                "choices 可以包含：按原描述直出、让用户提供英文 Danbooru tag、或从 last_tool_result.result.candidates 中确认一个候选。"
                "候选确认时使用 character_confirm；未确认前不要把候选当事实。\n\n"
            )
        return (
            research_rule
            +
            "完成必要 research 后，明确绘图请求应返回 prompt choices 让用户选择后再 dream，不得直接执行 dream。"
            "NSFW/成人内容请求更必须先返回 choices；每项 choice 必须包含 label、description、chain；chain 使用上面列出的 action。\n\n"
            "prompt choices 应参考用户长期偏好，"
            "并且必须包含一个原描述直接生成/按原描述生成选项；该选项的 dream.description 尽量保留用户原始绘图请求，不要扩写、改写或套用长期偏好。"
            "但当前明确要求优先；选项差异应围绕构图、姿态、表情、服装状态、场景、镜头重点和强度，"
            "不要以画风作为主要维度，画风由 artist tags / artist combo 控制。"
            "choice.label 不要写成 xx风、xx画风或 xx风格。"
            "dream.description 只写最终提示描述，不要包含推理、不确定问号、箭头改写或 X -> Y 这类纠错痕迹。"
            "每个 prompt choice 的 dream.description 必须完整可执行，并保留用户指定角色、动作、服装、LoRA、artist tags、尺寸和模式。\n\n"
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
            return ["character_resolve", "dream", "tagsite", "loras", "skill_load", "chat"]
        if name in ("continue_dream", "edit_dream"):
            return ["generation_info", "dream", "chat"]
        if name == "contextual_followup":
            return ["chat", "dream", "tagsite", "tags", "generation_info", "gallery"]
        if name in ("switch_model", "query_model"):
            return ["models", "chat"]
        if name == "add_provider":
            return ["add_provider", "models", "chat"]
        if name == "tool_continue":
            return ["chat", "dream", "character_resolve", "character_confirm", "tagsite", "models", "add_provider", "file_read", "file_write", "web_fetch"]
        if name == "command":
            return ["models", "add_provider", "status", "loras", "telegram", "webui", "gallery", "generation_info", "update", "clear",
                     "history", "artists", "character_resolve", "character_confirm", "tagsite", "tags", "config_get", "config_set",
                     "session_list", "session_switch", "session_new", "skill_list", "skill_load",
                     "llm_status", "llm_test", "chat",
                     "memory_set", "memory_get", "memory_forget", "memory_list"]
        if name == "chat":
            return ["chat", "status", "models", "gallery", "generation_info", "update", "history", "loras", "telegram",
                     "character_resolve", "character_confirm",
                     "memory_set", "memory_get", "memory_forget", "memory_list"]
        return ["dream", "models", "add_provider", "tagsite", "loras", "status", "chat"]

    def _schema_for(self, name):
        registry = getattr(self.host, "tool_registry", None)
        if registry and name != "chat":
            schema = registry.schema(name)
            if schema:
                return schema
        return TOOL_SCHEMAS.get(name)
