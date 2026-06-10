from .context import ContextBuilder
from .intent import IntentRouter
from .memory import AgentMemory
from .planner import LLMPlanner
from .repair import ActionRepair
from .validator import ActionValidator


class AgentPipeline:
    def __init__(self, host):
        self.host = host
        self.router = IntentRouter(host)
        self.context = ContextBuilder(host, self.router)
        self.planner = LLMPlanner(host)
        self.repair = ActionRepair(host)
        self.validator = ActionValidator(host)
        self.memory = AgentMemory(host)

    def process(self, user_input, source="cli", use_context=None):
        sid, session = self.host._session_current()
        conv = session["conversation"]
        system = self.host._agent_system_prompt()

        intent = self.router.route(user_input)

        ctx = self.context.build(intent, user_input, session, conv, use_context=use_context)
        if intent.name in ("continue_dream", "edit_dream") and not ctx.data.get("last_dream_params"):
            result = {"reply": "我还没有可续画的上一张，请先告诉我要画什么。", "action": "chat", "params": {}}
            self.memory.append_turn(session, user_input, result)
            self.host._save_sessions()
            return result

        agent_input = self.context.build_agent_input(intent, user_input, ctx)
        result = self.planner.plan(system, ctx.history, agent_input, intent=intent)
        if self._should_fallback_to_dream(intent, result):
            result = {
                "reply": "好的，按你的描述准备生成。",
                "action": "dream",
                "params": {"description": user_input},
            }
        result = self.validator.validate_and_repair(intent, user_input, result, ctx)
        result = self.repair.repair(intent, user_input, result, ctx)
        self.memory.append_turn(session, user_input, result)
        self.memory.check_summarize(session)
        self.host._save_sessions()
        return result

    def _should_fallback_to_dream(self, intent, result):
        if intent.name != "new_dream":
            return False
        if not isinstance(result, dict):
            return True
        chain = result.get("chain") or []
        if chain:
            return False
        action = result.get("action")
        reply = str(result.get("reply") or "").strip()
        return action in (None, "", "chat") and not reply
