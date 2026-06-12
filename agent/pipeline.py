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

        artifact_result = self._handle_artifact_reference(session, user_input)
        if artifact_result:
            self.state.update_after_plan(session, user_input, artifact_result)
            self.memory.append_turn(session, user_input, artifact_result)
            self.memory.check_summarize(session)
            self.host._save_sessions()
            return artifact_result

        intent = self.router.route(user_input)
        if intent.name != "tool_continue" and resolved.get("turn", {}).get("kind") in ("followup", "explain", "reaction", "selection", "cancel", "retry", "correction"):
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
        if self._is_research_chain(intent, result):
            self.state.mark_researching(session, user_input, self.host._extract_chain(result)[0])
        else:
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

    def _is_research_chain(self, intent, result):
        if getattr(intent, "name", "") != "new_dream":
            return False
        chain = self.host._extract_chain(result)
        return len(chain) == 1 and chain[0].get("action") in ("character_resolve", "tagsite", "tags")

    def _handle_artifact_reference(self, session, user_input):
        text = str(user_input or "").strip()
        if not text:
            return None
        artifact = self.state.last_artifact(session)
        generation = session.get("last_generation") or {}
        has_generation = bool(artifact or generation)
        if not has_generation:
            return None
        if text in ("打开", "打开看看", "打开看", "看看", "看一下"):
            return {"reply": "这是刚刚生成的结果。", "action": "gallery", "params": {}}
        if any(k in text for k in ("打开刚刚", "打开刚才", "打开上一", "打开这张", "打开图片", "打开画廊")):
            return {"reply": "这是刚刚生成的结果。", "action": "gallery", "params": {}}
        if any(k in text for k in ("刚刚画的是什么", "刚才画的是什么", "刚刚生成了什么", "刚才生成了什么", "我刚刚画的是什么", "我刚才画的是什么")):
            return {"reply": self.state.describe_artifact(artifact or self.state._artifact_from_generation(generation)), "action": "chat", "params": {}}
        if any(k in text for k in ("完整提示词", "提示词")):
            return {"reply": self._format_generation_info(generation, artifact, "prompt"), "action": "chat", "params": {}}
        if any(k in text for k in ("输出目录", "输出文件夹", "保存在哪", "在哪个文件夹")):
            return {"reply": self._format_generation_info(generation, artifact, "path"), "action": "chat", "params": {}}
        return None

    def _format_generation_info(self, generation, artifact, detail):
        artifact = artifact or self.state._artifact_from_generation(generation)
        if detail == "path":
            return f"批次目录：{artifact.get('run_dir') or '-'}"
        prompt = artifact.get("prompt") or (generation or {}).get("prompt") or ""
        if prompt:
            return f"完整提示词：\n{prompt}"
        return "当前记录里没有完整提示词。"
