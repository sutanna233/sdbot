import re


class ActionRepair:
    def __init__(self, host):
        self.host = host

    FALLBACK_ACTIONS = {
        "new_dream": ("dream", lambda user_input, ctx: {"description": user_input}),
        "switch_model": ("models", lambda user_input, ctx: {"action": "switch"}),
        "query_model": ("models", lambda user_input, ctx: {"action": "status"}),
    }

    def extract_requested_num(self, user_input):
        text = (user_input or "").strip()
        m = re.search(r"(\d+)\s*张", text)
        if m:
            return int(m.group(1))
        cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        m = re.search(r"([一两二三四五六七八九十])\s*张", text)
        if m:
            return cn.get(m.group(1))
        return None

    def is_quantity_only_continue(self, user_input):
        text = (user_input or "").strip()
        if any(k in text for k in ("加", "改", "换", "不要", "背景", "颜色", "头发", "衣服", "姿势")):
            return False
        return bool(re.search(r"(再画|再来|继续|继续画|再生成)\s*([0-9一两二三四五六七八九十]+)?\s*张?", text))

    def clean_reply(self, text):
        text = str(text or "")
        for bad in ("已解码你的需求，", "已解码你的需求", "解码用户输入后", "base64", "Base64"):
            text = text.replace(bad, "")
        return text.strip()

    def is_schema_leak_reply(self, text):
        text = str(text or "")
        leak_markers = (
            "schema", "必填", "allowed_params", "params", "action", "参数与必填项",
            "已记录", "已确认", "已收到使用说明", "使用说明", "动作和参数规范",
            "严格遵守", "参考信息", "后续按需调用", "确认删除", "不可恢复",
            "确认清理", "确认执行该操作", "已处理", "系统操作描述", "系统操作规范",
            "按照系统操作", "我会按照系统", "已收到，我会按照", "系统规范执行",
        )
        return any(m in text for m in leak_markers)

    def is_memory_query(self, user_input):
        text = str(user_input or "")
        markers = (
            "你记得什么", "你的记忆", "记忆是什么", "有什么记忆", "你都记得",
            "你知道我什么", "你还记得什么", "你记住了什么", "记得我什么",
            "memory", "remember about me",
        )
        return any(m in text.lower() for m in markers)

    def _schema_leak_fallback(self, intent, user_input, result, ctx):
        chain = self.host._extract_chain(result)
        if chain:
            return None
        if result.get("action") not in (None, "chat"):
            return None
        reply = result.get("reply", "")
        if reply and not self.is_schema_leak_reply(reply):
            return None
        if intent.name not in self.FALLBACK_ACTIONS:
            if self.is_memory_query(user_input):
                return {"reply": "我查一下当前记忆。", "action": "memory_list", "params": {}}
            return {"reply": "请重新说明你的需求，我用正确的工具帮你处理。", "action": "chat", "params": {}}
        action, params_factory = self.FALLBACK_ACTIONS[intent.name]
        return {
            "reply": "好的，已按你的请求准备执行。",
            "action": action,
            "params": params_factory(user_input, ctx),
        }

    def repair(self, intent, user_input, result, ctx):
        if not isinstance(result, dict):
            return result
        if "reply" in result:
            result["reply"] = self.clean_reply(result.get("reply", ""))
        last = ctx.data.get("last_dream_params") or {}
        if intent.name in ("continue_dream", "edit_dream") and last:
            chain = self.host._extract_chain(result)
            if not chain and intent.name == "continue_dream":
                params = dict(last)
                n = self.extract_requested_num(user_input)
                if n:
                    params["num"] = n
                return {"reply": f"好的，沿用上一张描述再画{params.get('num', 1)}张。", "action": "dream", "params": params}
            if len(chain) == 1 and chain[0].get("action") == "dream":
                params = dict(chain[0].get("params") or {})
                n = self.extract_requested_num(user_input)
                if self.is_quantity_only_continue(user_input):
                    fixed = dict(last)
                    fixed["num"] = n or params.get("num") or last.get("num", 1)
                    chain[0]["params"] = fixed
                    result["action"] = "dream"
                    result["params"] = fixed
                    result.pop("chain", None)
                    result["reply"] = f"好的，沿用上一张描述再画{fixed.get('num', 1)}张。"
                else:
                    if not params.get("description"):
                        params["description"] = last.get("description", "")
                    if n:
                        params["num"] = n
                    chain[0]["params"] = params
                    if result.get("action") == "dream":
                        result["params"] = params
        return result
