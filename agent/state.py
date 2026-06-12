import json
import re
from datetime import datetime


def _now():
    return datetime.now().isoformat(timespec="seconds")


class ConversationState:
    STATUS_RESEARCHING = "researching"
    STATUS_RESEARCH_DONE = "research_done"
    STATUS_RESEARCH_FAILED = "research_failed"
    STATUS_WAITING_CHOICE = "waiting_choice"
    STATUS_EXECUTING = "executing"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_FAILED = "failed"

    def __init__(self, host):
        self.host = host

    def get(self, session):
        state = session.setdefault("conversation_state", {})
        state.setdefault("active_task", None)
        state.setdefault("last_choices", None)
        state.setdefault("last_generation", session.get("last_generation"))
        state.setdefault("last_search", None)
        state.setdefault("last_tool_result", None)
        state.setdefault("last_assistant_message", "")
        state.setdefault("last_user_message", "")
        return state

    def classify_turn(self, user_input, state):
        text = str(user_input or "").strip()
        lower = text.lower()
        if not text:
            return {"kind": "reaction", "reason": "empty"}
        if re.fullmatch(r"[？?]+", text) or text in ("何意味", "什么意思", "啥意思", "什么情况"):
            return {"kind": "explain", "reason": "short_question"}
        if self._is_emoji_only(text):
            return {"kind": "reaction", "reason": "emoji"}
        if text in ("取消", "算了", "不用了", "不要了"):
            return {"kind": "cancel", "reason": "cancel_word"}
        if text in ("重试", "再试试", "重来", "再来一次", "retry"):
            return {"kind": "retry", "reason": "retry_word"}
        if re.fullmatch(r"(第)?[一二三四五六七八九十123456789]\s*(个|项)?", text) or re.fullmatch(r"(选|选择)\s*[一二三四五六七八九十123456789]", text):
            return {"kind": "selection", "reason": "choice_index"}
        if any(k in text for k in ("不是这个", "不对", "错了", "搞错了")):
            return {"kind": "correction", "reason": "correction_word"}
        if self._looks_like_followup(text, lower, state):
            return {"kind": "followup", "reason": "contextual_modifier"}
        return {"kind": "new_task", "reason": "default"}

    def resolve(self, user_input, state):
        turn = self.classify_turn(user_input, state)
        kind = turn["kind"]
        text = str(user_input or "").strip()
        resolved = {"turn": turn, "refers_to": None, "operation": None, "patch": {}}
        if kind == "explain":
            resolved.update({"refers_to": "last_assistant_message", "operation": "explain"})
        elif kind == "reaction":
            resolved.update({"refers_to": "last_assistant_message", "operation": "react"})
        elif kind == "selection":
            resolved.update({"refers_to": "last_choices", "operation": "select", "patch": {"selected_index": self._extract_index(text)}})
        elif kind == "cancel":
            resolved.update({"refers_to": "active_task", "operation": "cancel"})
        elif kind == "retry":
            resolved.update({"refers_to": "last_tool_result", "operation": "retry"})
        elif kind == "correction":
            resolved.update({"refers_to": "active_task", "operation": "correct"})
        elif kind == "followup":
            if any(k in text for k in ("英文搜", "英文查", "用英文", "换英文")):
                resolved.update({"refers_to": "last_search", "operation": "search_again", "patch": {"language": "en"}})
            elif state.get("last_choices"):
                resolved.update({"refers_to": "last_choices", "operation": "modify_choices", "patch": {"modifier": text}})
            else:
                resolved.update({"refers_to": "active_task", "operation": "modify", "patch": {"modifier": text}})
        return resolved

    def render_for_prompt(self, state, resolved=None):
        safe = {
            "active_task": state.get("active_task"),
            "last_choices": self._trim_choices(state.get("last_choices")),
            "last_generation": self._trim_generation(state.get("last_generation")),
            "last_search": state.get("last_search"),
            "last_tool_result": self._trim_tool(state.get("last_tool_result")),
            "last_assistant_message": str(state.get("last_assistant_message") or "")[:600],
        }
        return json.dumps({"resolved_turn": resolved or {}, "conversation_state": safe}, ensure_ascii=False)

    def update_after_plan(self, session, user_input, result):
        state = self.get(session)
        state["last_user_message"] = str(user_input or "")[:1000]
        if isinstance(result, dict):
            reply = result.get("reply")
            if reply:
                state["last_assistant_message"] = str(reply)[:1000]
            choices = result.get("choices") or []
            if choices:
                state["last_choices"] = {
                    "kind": "prompt" if self._choices_have_dream(choices) else "generic",
                    "original_request": str(user_input or ""),
                    "choices": choices[:5],
                    "selected_index": None,
                    "cancelled": False,
                    "created_at": _now(),
                }
                state["active_task"] = self._task_from_choices(user_input, choices, self.STATUS_WAITING_CHOICE)
            else:
                task = self._task_from_result(user_input, result, status="planning")
                if task:
                    state["active_task"] = task
        session["conversation_state"] = state

    def save_choices(self, session, user_input, choices):
        state = self.get(session)
        state["last_choices"] = {
            "kind": "prompt" if self._choices_have_dream(choices) else "generic",
            "original_request": str(user_input or ""),
            "choices": (choices or [])[:5],
            "selected_index": None,
            "cancelled": False,
            "created_at": _now(),
        }
        state["active_task"] = self._task_from_choices(user_input, choices, self.STATUS_WAITING_CHOICE)
        session["conversation_state"] = state

    def mark_choice(self, session, index=None, cancelled=False):
        state = self.get(session)
        choices = state.get("last_choices") or {}
        if choices:
            choices["selected_index"] = index
            choices["cancelled"] = bool(cancelled)
            state["last_choices"] = choices
        task = state.get("active_task") or {}
        if task:
            task["status"] = self.STATUS_CANCELLED if cancelled else self.STATUS_EXECUTING
            task["updated_at"] = _now()
            state["active_task"] = task
        session["conversation_state"] = state

    def save_generation(self, session, generation):
        state = self.get(session)
        state["last_generation"] = generation
        task = state.get("active_task") or {}
        params = generation.get("params") if isinstance(generation, dict) else {}
        task.update({
            "type": "generation",
            "goal": (params or {}).get("description") or (generation or {}).get("description") or task.get("goal", "生成图片"),
            "status": self.STATUS_COMPLETED,
            "updated_at": _now(),
        })
        state["active_task"] = task
        session["conversation_state"] = state

    def save_tool_result(self, session, step, action_result):
        state = self.get(session)
        action = step.get("action") if isinstance(step, dict) else ""
        params = step.get("params", {}) if isinstance(step, dict) else {}
        summary = action_result.get("summary", "") if isinstance(action_result, dict) else ""
        output = action_result.get("output", []) if isinstance(action_result, dict) else []
        result = action_result.get("result", {}) if isinstance(action_result, dict) else {}
        tool = {
            "action": action,
            "params": params,
            "ok": bool(action_result.get("ok", True)) if isinstance(action_result, dict) else True,
            "summary": summary,
            "output": output[-20:] if isinstance(output, list) else [],
            "result": self._trim_result(result),
            "created_at": _now(),
        }
        state["last_tool_result"] = tool
        session.setdefault("tool_history", []).append(tool)
        session["tool_history"] = session["tool_history"][-20:]
        active = state.get("active_task") or {}
        if action in ("tagsite", "tags"):
            query = params.get("names") or params.get("keyword") or ""
            state["last_search"] = {
                "query": query,
                "tool": action,
                "status": "ok" if tool["ok"] else "failed",
                "summary": summary,
                "created_at": _now(),
            }
            if active.get("type") == "generation" and active.get("status") == self.STATUS_RESEARCHING:
                active["status"] = self.STATUS_RESEARCH_DONE if tool["ok"] else self.STATUS_RESEARCH_FAILED
                active["research"] = state["last_search"]
                active["updated_at"] = _now()
                state["active_task"] = active
            else:
                state["active_task"] = {
                    "type": "search",
                    "goal": "搜索信息",
                    "subject": query,
                    "constraints": {},
                    "status": self.STATUS_COMPLETED if tool["ok"] else self.STATUS_FAILED,
                    "updated_at": _now(),
                }
        session["conversation_state"] = state

    def mark_researching(self, session, user_input, step):
        state = self.get(session)
        params = step.get("params", {}) if isinstance(step, dict) else {}
        state["active_task"] = {
            "type": "generation",
            "goal": str(user_input or ""),
            "subject": params.get("names") or str(user_input or ""),
            "constraints": {},
            "status": self.STATUS_RESEARCHING,
            "created_at": _now(),
            "updated_at": _now(),
        }
        session["conversation_state"] = state

    def _is_emoji_only(self, text):
        stripped = re.sub(r"[\s\ufe0f\u200d]+", "", text)
        if not stripped:
            return False
        return not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", stripped)

    def _looks_like_followup(self, text, lower, state):
        markers = ("更", "再", "换", "改", "用英文", "英文搜", "英文查", "换英文", "就这个", "这个", "继续", "加强", "弱一点", "强一点")
        if any(m in text for m in markers) or any(m in lower for m in ("more", "retry", "again", "english")):
            return bool(state.get("active_task") or state.get("last_choices") or state.get("last_search") or state.get("last_generation"))
        return False

    def _extract_index(self, text):
        cn = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "七": 6, "八": 7, "九": 8, "十": 9}
        m = re.search(r"([1-9])", text)
        if m:
            return int(m.group(1)) - 1
        for key, val in cn.items():
            if key in text:
                return val
        return None

    def _choices_have_dream(self, choices):
        for choice in choices or []:
            for step in choice.get("chain") or []:
                if step.get("action") == "dream":
                    return True
        return False

    def _task_from_result(self, user_input, result, status="planning"):
        chain = self.host._extract_chain(result)
        action = chain[0].get("action") if chain else result.get("action")
        if not action or action == "chat":
            return None
        typ = "generation" if action == "dream" else "search" if action in ("tagsite", "tags") else "tool"
        params = chain[0].get("params", {}) if chain else result.get("params", {})
        return {
            "type": typ,
            "goal": params.get("description") or str(user_input or ""),
            "subject": params.get("description") or params.get("keyword") or params.get("names") or "",
            "constraints": {},
            "status": status,
            "created_at": _now(),
            "updated_at": _now(),
        }

    def _task_from_choices(self, user_input, choices, status):
        return {
            "type": "generation" if self._choices_have_dream(choices) else "tool",
            "goal": str(user_input or ""),
            "subject": str(user_input or ""),
            "constraints": {},
            "status": status,
            "created_at": _now(),
            "updated_at": _now(),
        }

    def _trim_choices(self, choices):
        if not isinstance(choices, dict):
            return choices
        copy = dict(choices)
        copy["choices"] = [
            {"label": c.get("label"), "description": c.get("description"), "chain": c.get("chain")}
            for c in (copy.get("choices") or [])[:5]
        ]
        return copy

    def _trim_generation(self, generation):
        if not isinstance(generation, dict):
            return generation
        return {
            "description": generation.get("description"),
            "prompt": str(generation.get("prompt") or "")[:800],
            "params": generation.get("params"),
            "run": generation.get("run"),
        }

    def _trim_tool(self, tool):
        if not isinstance(tool, dict):
            return tool
        return {k: tool.get(k) for k in ("action", "params", "ok", "summary", "output", "result")}

    def _trim_result(self, result):
        if not isinstance(result, dict):
            return result
        if "matches" in result:
            copy = {
                "query": result.get("query"),
                "missing": result.get("missing"),
                "tags": (result.get("tags") or [])[:80],
                "matches": [],
            }
            for item in (result.get("matches") or [])[:5]:
                if not isinstance(item, dict):
                    continue
                copy["matches"].append({
                    "query": item.get("query"),
                    "name": item.get("name"),
                    "tags": (item.get("tags") or [])[:80],
                    "tag_count": item.get("tag_count"),
                })
            return copy
        return {k: result.get(k) for k in list(result.keys())[:20]}
