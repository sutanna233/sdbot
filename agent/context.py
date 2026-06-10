import json

from .types import AgentContext


class ContextBuilder:
    def __init__(self, host, router):
        self.host = host
        self.router = router

    def get_last_dream_params(self, session):
        params = session.get("last_dream_params") or {}
        return dict(params) if isinstance(params, dict) else {}

    def build(self, intent, user_input, session, conversation, use_context=None):
        if intent.name == "tool_continue":
            return AgentContext("recent_tool_result", conversation[-6:], {})
        if intent.name == "add_provider":
            return AgentContext("short_chat_history", conversation[-6:], {})
        if intent.name in ("continue_dream", "edit_dream"):
            return AgentContext("last_dream_only", [], {"last_dream_params": self.get_last_dream_params(session)})
        if intent.name == "chat" and (self.router.should_use_context(user_input) if use_context is None else use_context):
            return AgentContext("short_chat_history", conversation[-6:], {})
        return AgentContext("no_history", [], {})

    def build_agent_input(self, intent, user_input, ctx):
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
