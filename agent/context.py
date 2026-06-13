import json

from logging_setup import get_logger
from .types import AgentContext

logger = get_logger("agent.context")


class ContextBuilder:
    def __init__(self, host, router):
        self.host = host
        self.router = router

    def get_last_dream_params(self, session):
        params = session.get("last_dream_params") or {}
        return dict(params) if isinstance(params, dict) else {}

    def build(self, intent, user_input, session, conversation, use_context=None, resolved=None):
        logger.debug("build: intent=%s use_context=%s conv_len=%d",
                     intent.name, use_context, len(conversation or []))
        state = session.get("conversation_state") or {}
        data = {
            "conversation_state": state,
            "resolved_turn": resolved or {},
            "last_dream_params": self.get_last_dream_params(session),
            "last_generation": session.get("last_generation") or state.get("last_generation"),
            "last_choices": state.get("last_choices"),
            "last_search": state.get("last_search"),
            "last_tool_result": state.get("last_tool_result"),
        }
        if intent.name == "tool_continue":
            return AgentContext("recent_tool_result", [], data)
        if intent.name == "add_provider":
            return AgentContext("short_chat_history", self._clean_recent(conversation, 6), data)
        if intent.name in ("continue_dream", "edit_dream"):
            return AgentContext("last_dream_only", self._clean_recent(conversation, 4), data)
        if intent.name == "contextual_followup":
            return AgentContext("stateful_followup", self._clean_recent(conversation, 10), data)
        if intent.name == "chat" and (self.router.should_use_context(user_input) if use_context is None else use_context):
            return AgentContext("short_chat_history", self._clean_recent(conversation, 8), data)
        if intent.name == "chat":
            return AgentContext("short_chat_history", self._clean_recent(conversation, 4), data)
        return AgentContext("no_history", [], data)

    def build_agent_input(self, intent, user_input, ctx):
        if intent.name == "tool_continue":
            state_text = self.host.agent.state.render_for_prompt(
                ctx.data.get("conversation_state") or {},
                ctx.data.get("resolved_turn") or {},
            )
            return (
                "[工具输出上下文]\n"
                f"{state_text}\n"
                "规则：基于 last_tool_result 判断下一步。"
                "如果 active_task.type=generation 且 status=research_done，必须基于搜索结果生成 prompt choices，"
                "优先使用 last_tool_result.result.matches[].tags / result.tags 中的结构化标签；"
                "不要再次搜索，不要直接 dream；未查到时不得脑补角色服装或设定。\n"
                "如果 status=needs_character_confirmation，必须先让用户确认角色 tag、提供英文 tag，或选择按原描述直出；"
                "不得生成姿势/构图类 prompt choices。\n"
                f"[用户请求]\n{user_input}"
            )
        if intent.name == "contextual_followup":
            state_text = self.host.agent.state.render_for_prompt(
                ctx.data.get("conversation_state") or {},
                ctx.data.get("resolved_turn") or {},
            )
            return (
                "[上下文解析]\n"
                f"{state_text}\n"
                "规则：用户这句话是上下文追问/反应/选择/修正，不要当作新的绘图请求。"
                "优先根据 resolved_turn.refers_to 和 operation 处理。"
                "如果 operation=explain，解释上一条助手回复；如果 operation=search_again，基于 last_search 继续；"
                "如果 operation=modify_choices 或 modify，基于 active_task/last_choices 修改，不要反问对象。\n"
                f"[用户请求]\n{user_input}"
            )
        if intent.name not in ("continue_dream", "edit_dream"):
            return user_input
        last = ctx.data.get("last_dream_params") or {}
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

    def _clean_recent(self, conversation, count):
        result = []
        for item in reversed(conversation or []):
            content = str(item.get("content", ""))
            if content.startswith("[Tool Result") or "判断任务是否完成" in content:
                continue
            result.append(item)
            if len(result) >= count:
                break
        return list(reversed(result))
