import unittest
import tempfile
from pathlib import Path

from agent.pipeline import AgentPipeline
from agent.schemas import TOOL_SCHEMAS
from tools.characters import CharacterConfirmTool, CharacterResolveTool, CharacterResolver
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
            {"reply": "先解析角色。", "chain": [{"action": "character_resolve", "params": {"request": "帮我画一个明日方舟的普瑞赛斯", "characters": ["普瑞赛斯"], "works": ["明日方舟"]}}]},
            {"reply": "查到资料后给你几个方向。", "choices": [
                {"label": "原描述直出", "description": "保留原描述", "chain": [
                    {"action": "dream", "params": {"description": "明日方舟 普瑞赛斯 priestess_(arknights)"}}
                ]}
            ]},
        ])

        first = host.agent.process("帮我画一个明日方舟的普瑞赛斯")
        self.assertEqual(host._extract_chain(first)[0]["action"], "character_resolve")
        self.assertEqual(host.session["conversation_state"]["active_task"]["status"], "researching")

        step = host._extract_chain(first)[0]
        host.agent.state.save_tool_result(host.session, step, {
            "ok": True,
            "summary": "找到普瑞赛斯",
            "output": ["tags"],
            "result": {
                "request": "帮我画一个明日方舟的普瑞赛斯",
                "characters": ["普瑞赛斯"],
                "works": ["明日方舟"],
                "status": "resolved",
                "resolved": [{"input": "普瑞赛斯", "tag": "priestess_(arknights)", "name": "Priestess", "tags": ["priestess_(arknights)"], "tag_count": 1}],
                "candidates": [],
                "unresolved": [],
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

    def test_character_resolver_uses_confirmed_alias_without_hardcoding(self):
        class TagSite:
            def __init__(self):
                self._cache = {}

            def search_character(self, tag):
                return {"name": tag, "tags": [tag, "work_tag"]}

        class Host:
            def __init__(self, root):
                self.script_dir = Path(root)
                self.tag_site = TagSite()

        with tempfile.TemporaryDirectory() as tmp:
            host = Host(tmp)
            host.character_resolver = CharacterResolver(host)
            confirmed = CharacterConfirmTool(host)({"alias": "角色A", "tag": "role_a_(work)", "work": "作品A"})
            self.assertTrue(confirmed["ok"])

            result = CharacterResolveTool(host)({"request": "画作品A的角色A", "characters": ["角色A"], "works": ["作品A"]})
            self.assertEqual(result["status"], "resolved")
            self.assertEqual(result["resolved"][0]["tag"], "role_a_(work)")

    def test_character_resolver_unresolved_does_not_guess(self):
        class TagSite:
            _cache = {}

            def search_character(self, tag):
                raise AssertionError("resolver should not call tagsite for unresolved non-tag input")

        class Host:
            script_dir = None
            tag_site = TagSite()

        with tempfile.TemporaryDirectory() as tmp:
            host = Host()
            host.script_dir = Path(tmp)
            host.character_resolver = CharacterResolver(host)
            result = CharacterResolveTool(host)({"request": "画作品B的角色B", "characters": ["角色B"], "works": ["作品B"]})
            self.assertEqual(result["status"], "unresolved")
            self.assertEqual(result["unresolved"], ["角色B"])

    def test_character_resolver_discovers_and_verifies_llm_candidate(self):
        class LLM:
            def agent_chat(self, system, conversation, user_input):
                return {"candidates": ["amiya_(arknights)", "amiya"]}

        class TagSite:
            def __init__(self):
                self._cache = {}

            def search_character(self, tag):
                if tag == "amiya_(arknights)":
                    return {"name": "amiya_(arknights)", "tags": ["amiya_(arknights)", "arknights", "1girl"]}
                return None

        class Host:
            def __init__(self, root):
                self.script_dir = Path(root)
                self.tag_site = TagSite()

            def _llm(self):
                return LLM()

        with tempfile.TemporaryDirectory() as tmp:
            host = Host(tmp)
            host.character_resolver = CharacterResolver(host)
            result = CharacterResolveTool(host)({"request": "画一张明日方舟的阿米娅", "characters": ["阿米娅"], "works": ["明日方舟"]})
            self.assertEqual(result["status"], "resolved")
            self.assertEqual(result["resolved"][0]["tag"], "amiya_(arknights)")
            self.assertEqual(result["resolved"][0]["source"], "llm_candidate_verified+work")


if __name__ == "__main__":
    unittest.main()
