from .context import ContextBuilder
from .intent import IntentRouter
from .memory import AgentMemory
from .planner import LLMPlanner
from .repair import ActionRepair
from .state import ConversationState
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
        self.state = ConversationState(host)

    def process(self, user_input, source="cli", use_context=None):
        sid, session = self.host._session_current()
        conv = session["conversation"]
        system = self.host._agent_system_prompt()
        state = self.state.get(session)
        resolved = self.state.resolve(user_input, state)

        intent = self.router.route(user_input)
        if resolved.get("turn", {}).get("kind") in ("followup", "explain", "reaction", "selection", "cancel", "retry", "correction"):
            intent.name = "contextual_followup"

        ctx = self.context.build(intent, user_input, session, conv, use_context=use_context, resolved=resolved)
        direct = self._handle_direct_followup(session, user_input, resolved)
        if direct:
            if not direct.get("chain"):
                self.state.update_after_plan(session, user_input, direct)
            self.memory.append_turn(session, user_input, direct)
            self.memory.check_summarize(session)
            self.host._save_sessions()
            return direct
        if intent.name in ("continue_dream", "edit_dream") and not ctx.data.get("last_dream_params"):
            result = {"reply": "我还没有可续画的上一张，请先告诉我要画什么。", "action": "chat", "params": {}}
            self.memory.append_turn(session, user_input, result)
            self.host._save_sessions()
            return result

        agent_input = self.context.build_agent_input(intent, user_input, ctx)
        result = self.planner.plan(system, ctx.history, agent_input, intent=intent, ctx=ctx)
        if self._should_fallback_to_dream(intent, result):
            result = {
                "reply": "好的，按你的描述准备生成。",
                "action": "dream",
                "params": {"description": user_input},
            }
        result = self.validator.validate_and_repair(intent, user_input, result, ctx)
        result = self.repair.repair(intent, user_input, result, ctx)
        self.state.update_after_plan(session, user_input, result)
        self.memory.append_turn(session, user_input, result)
        self.memory.check_summarize(session)
        self.host._save_sessions()
        return result

    def _handle_direct_followup(self, session, user_input, resolved):
        turn = resolved.get("turn", {}) if isinstance(resolved, dict) else {}
        kind = turn.get("kind")
        state = self.state.get(session)
        if kind == "cancel":
            self.state.mark_choice(session, cancelled=True)
            return {"reply": "已取消当前选择。", "action": "chat", "params": {}}
        if kind == "selection":
            choices_state = state.get("last_choices") or {}
            choices = choices_state.get("choices") or []
            idx = (resolved.get("patch") or {}).get("selected_index")
            if idx is None or idx < 0 or idx >= len(choices):
                return {"reply": "我没找到这个选项，请重新选择编号。", "action": "chat", "params": {}}
            choice = choices[idx]
            chain = choice.get("chain") or []
            if not chain:
                return {"reply": "这个选项没有可执行步骤。", "action": "chat", "params": {}}
            self.state.mark_choice(session, index=idx, cancelled=False)
            return {"reply": f"已选择：{choice.get('label', f'选项 {idx + 1}')}。", "chain": chain}
        return None

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
