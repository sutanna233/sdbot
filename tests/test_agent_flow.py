import unittest

from agent.pipeline import AgentPipeline
from agent.schemas import TOOL_SCHEMAS
from tools.tagsite import TagSiteTool


class FakeRegistry:
    def names(self):
        return list(TOOL_SCHEMAS.keys())

    def schema(self, name):
        return TOOL_SCHEMAS.get(name)


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def agent_chat(self, system, history, agent_input):
        self.prompts.append(agent_input)
        if not self.responses:
            raise AssertionError("No fake LLM response left")
        return self.responses.pop(0)


class FakeHost:
    def __init__(self, responses):
        self.session = {"conversation": [], "name": "test"}
        self.tool_registry = FakeRegistry()
        self.llm = FakeLLM(responses)
        self.saved = 0
        self.agent = AgentPipeline(self)

    def _session_current(self):
        return "s", self.session

    def _agent_system_prompt(self):
        return "system"

    def _llm(self):
        return self.llm

    def _extract_chain(self, result):
        if not isinstance(result, dict):
            return []
        chain = result.get("chain") or []
        if not chain and result.get("action") and result.get("action") != "chat":
            chain = [{"action": result["action"], "params": result.get("params", {})}]
        return chain

    def _save_sessions(self):
        self.saved += 1


class AgentFlowTests(unittest.TestCase):
    def test_character_dream_research_then_choices(self):
        host = FakeHost([
            {"reply": "先查角色资料。", "chain": [{"action": "tagsite", "params": {"names": ["普瑞赛斯"]}}]},
            {"reply": "查到资料后给你几个方向。", "choices": [
                {"label": "原描述直出", "description": "保留原描述", "chain": [
                    {"action": "dream", "params": {"description": "明日方舟 普瑞赛斯 priestess_(arknights)"}}
                ]}
            ]},
        ])

        first = host.agent.process("帮我画一个明日方舟的普瑞赛斯")
        self.assertEqual(host._extract_chain(first)[0]["action"], "tagsite")
        self.assertEqual(host.session["conversation_state"]["active_task"]["status"], "researching")

        step = host._extract_chain(first)[0]
        host.agent.state.save_tool_result(host.session, step, {
            "ok": True,
            "summary": "找到普瑞赛斯",
            "output": ["tags"],
            "result": {
                "query": ["普瑞赛斯"],
                "matches": [{"query": "普瑞赛斯", "name": "Priestess", "tags": ["priestess_(arknights)"], "tag_count": 1}],
                "missing": [],
                "tags": ["priestess_(arknights)"],
            },
        })
        self.assertEqual(host.session["conversation_state"]["active_task"]["status"], "research_done")

        second = host.agent.process("继续。基于上一步的工具输出决定下一步。", source="tool_result")
        self.assertTrue(second.get("choices"))
        self.assertIn("last_tool_result", host.llm.prompts[-1])
        self.assertEqual(host.session["conversation_state"]["active_task"]["status"], "waiting_choice")

    def test_choice_selection_returns_saved_chain(self):
        host = FakeHost([])
        _, session = host._session_current()
        choice_chain = [{"action": "dream", "params": {"description": "cat girl"}}]
        host.agent.state.save_choices(session, "画猫娘", [
            {"label": "原描述", "description": "直接画", "chain": choice_chain}
        ])

        result = host.agent.process("第一个")
        self.assertEqual(result.get("chain"), choice_chain)
        self.assertEqual(session["conversation_state"]["last_choices"]["selected_index"], 0)
        self.assertEqual(session["conversation_state"]["active_task"]["status"], "executing")

    def test_tagsite_tool_returns_structured_result(self):
        class Host:
            def cmd_tagsite(self, *names):
                return {"query": list(names), "matches": [{"name": names[0], "tags": ["tag_a"]}], "missing": [], "tags": ["tag_a"]}

        result = TagSiteTool(Host())({"names": "amiya"})
        self.assertEqual(result["query"], ["amiya"])
        self.assertEqual(result["tags"], ["tag_a"])


if __name__ == "__main__":
    unittest.main()
