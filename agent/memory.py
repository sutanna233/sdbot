import json

from logging_setup import get_logger

SUMMARY_THRESHOLD = 24
logger = get_logger("agent.memory")


class GlobalMemory:
    def __init__(self, host, namespace):
        self.host = host
        self.namespace = namespace

    def _store(self):
        return self.host.sessions.setdefault("global_memory", {}).setdefault(self.namespace, {})

    def get(self, key):
        return self._store().get(key)

    def set(self, key, value):
        self._store()[key] = value
        self.host._save_sessions()

    def delete(self, key):
        self._store().pop(key, None)
        self.host._save_sessions()

    def get_all(self):
        return dict(self._store())

    def keys(self):
        return list(self._store().keys())


class AgentMemory:
    def __init__(self, host):
        self.host = host

    def save_last_dream_params_from_result(self, result, session):
        chain = self.host._extract_chain(result)
        last = None
        for step in chain:
            if step.get("action") == "dream":
                params = dict(step.get("params") or {})
                if params:
                    last = params
        if last is not None:
            session["last_dream_params"] = last

    def append_turn(self, session, user_input, result):
        conv = session["conversation"]
        conv.append({"role": "user", "content": user_input})
        conv.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})
        self.save_last_dream_params_from_result(result, session)
        logger.debug("append_turn: conv_len=%d result_action=%s", len(conv), result.get("action") if isinstance(result, dict) else "?")

    def check_summarize(self, session):
        conv = session.get("conversation", [])
        if len(conv) < SUMMARY_THRESHOLD:
            return
        mid = len(conv) * 3 // 5
        old_part = conv[:mid]
        session["conversation"] = conv[mid:]
        try:
            summary = self._summarize(old_part, session.get("summary", ""))
            session["summary"] = summary
            logger.info("Summarized conversation: kept=%d summarized=%d", len(conv) - mid, mid)
        except Exception:
            pass

    def _summarize(self, turns, existing_summary=""):
        text = "\n".join(f"{t['role']}: {str(t['content'])[:500]}" for t in turns)
        system = (
            "你是一个对话史官。压缩以下对话为 80 字中文摘要，保留："
            "用户最终达成了什么、表达的好恶、关键决策。输出纯文本。"
        )
        if existing_summary:
            user = f"已有摘要：{existing_summary}\n\n新对话：\n{text}"
        else:
            user = f"对话：\n{text}"
        try:
            result = self.host._llm().summarize(system, user)
            return result[:500] if result else text[:200]
        except Exception:
            return text[:200]
