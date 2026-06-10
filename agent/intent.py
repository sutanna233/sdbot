import re

from .types import Intent


class IntentRouter:
    def __init__(self, host):
        self.host = host

    def should_use_context(self, user_input):
        text = (user_input or "").strip().lower()
        markers = [
            "刚才", "上一", "上次", "继续", "再加", "加上", "加点", "改成", "换成",
            "这张", "那张", "前面", "之前", "同样", "还是", "再来", "再画", "多一点", "少一点",
            "more", "continue", "same", "previous", "last",
        ]
        return any(m in text for m in markers)

    def route(self, user_input):
        text = (user_input or "").strip()
        lower = text.lower()
        if text.startswith("继续。基于上一步的工具输出决定下一步"):
            return Intent("tool_continue")
        if self._provider_intent(text, lower):
            return Intent("add_provider")
        model_intent = self._model_intent(text)
        if model_intent:
            return model_intent
        if re.search(r"(再画|再来|继续|继续画|再生成)\s*\d*\s*张?", text):
            return Intent("continue_dream")
        if any(k in text for k in ("完整提示词", "提示词", "生成参数", "输出目录", "输出文件夹", "刚才生成", "刚刚生成")):
            return Intent("command", slots={"sub_intent": "generation_info"})
        if re.fullmatch(r"(就)?(这张|那张|上一张|上次|刚才)(啊|呀|呢|吧|嘛)?[？?。！!]*", text):
            return Intent("command", slots={"sub_intent": "generation_info"})
        if any(k in text for k in ("刚才", "上一", "上次", "这张", "那张", "再加", "加上", "加点", "改成", "换成", "多一点", "少一点")):
            return Intent("edit_dream")
        if any(k in text for k in ("画", "生成", "出图", "涩图", "图片", "图")):
            return Intent("new_dream")
        cmd_intent = self._command_intent(text, lower)
        if cmd_intent:
            return cmd_intent
        return Intent("chat")

    def _model_intent(self, text):
        lower = text.lower()
        if any(k in text for k in ("什么模型", "当前模型", "现在用的模型", "现在用的是什么模型")) or "model status" in lower:
            return Intent("query_model")
        if not any(k in text for k in ("模型", "model", "minimax", "deepseek", "gpt")):
            return None
        if not any(k in text for k in ("切换", "切到", "换到", "换成", "改成", "使用", "用")):
            return None
        role = "chat"
        if any(k in text for k in ("识图", "视觉", "vision")):
            role = "vision"
        if any(k in text for k in ("对话", "聊天", "chat")):
            role = "chat"
        model = self._extract_model_hint(text)
        return Intent("switch_model", slots={"role": role, "model_hint": model or ""})

    def _provider_intent(self, text, lower):
        if any(k in text for k in ("服务商", "模型服务", "模型渠道", "接入", "添加 API", "添加api", "新的模型")):
            return True
        if "/v1" in lower or "sk-" in lower:
            return True
        if any(k in lower for k in ("provider", "base_url", "/v1", "sk-")) and any(k in text for k in ("添加", "接入", "配置", "新的", "服务商")):
            return True
        return False

    def _extract_model_hint(self, text):
        m = re.search(r"(?:切换到|切到|换到|换成|改成|使用|用)\s*([A-Za-z0-9_.:-]+)", text)
        if m:
            return m.group(1).strip()
        parts = re.findall(r"[A-Za-z0-9_.:-]+", text)
        return parts[-1] if parts else ""

    def _resolve_model_key(self, hint):
        if not hint:
            return None
        models = self.host._get_models_list()
        if hint in models:
            return hint
        low = hint.lower()
        matches = [k for k in models if low in k.lower() or low in str(models[k].get("model", "")).lower()]
        if len(matches) == 1:
            return matches[0]
        return None

    def _command_intent(self, text, lower):
        """Route to intent name only; LLM decides concrete action from tool cards."""
        if any(k in lower for k in ("status", "状态")):
            return Intent("command", slots={"sub_intent": "status"})
        if any(k in lower for k in ("models", "model")):
            return Intent("command", slots={"sub_intent": "models"})
        if "telegram" in lower or "tg" in lower:
            return Intent("command", slots={"sub_intent": "telegram"})
        if "lora" in lower:
            return Intent("command", slots={"sub_intent": "loras"})
        if "history" in lower or "历史" in text:
            return Intent("command", slots={"sub_intent": "history"})
        if any(k in text for k in ("打开", "画廊", "看看结果", "查看结果", "最新画廊", "gallery")) or text.strip() in ("看看", "看一下", "打开看看", "打开看"):
            return Intent("command", slots={"sub_intent": "gallery"})
        if self._looks_like_clear(text, lower):
            return Intent("command", slots={"sub_intent": "clear"})
        if "角色" in text and any(k in text for k in ("搜", "查", "找")):
            return Intent("command", slots={"sub_intent": "tagsite"})
        if re.match(r"^(搜索|搜|查找|查询|查|找)\s*(.+)\s*(tag|tags|标签)$", text, re.I):
            return Intent("command", slots={"sub_intent": "tagsite"})
        if any(k in lower for k in ("webui", "网页", "web界面")):
            return Intent("command", slots={"sub_intent": "webui"})
        if any(k in text for k in ("完整提示词", "提示词", "生成参数", "输出目录", "输出文件夹", "刚才生成", "刚刚生成")):
            return Intent("command", slots={"sub_intent": "generation_info"})
        if "artist" in lower or "艺术家" in text:
            return Intent("command", slots={"sub_intent": "artists"})
        if "skill" in lower:
            return Intent("command", slots={"sub_intent": "skill"})
        if "session" in lower or "会话" in text:
            return Intent("command", slots={"sub_intent": "session"})
        if "config" in lower or "配置" in text:
            return Intent("command", slots={"sub_intent": "config"})
        return None

    def _looks_like_clear(self, text, lower):
        has_target = any(k in lower for k in ("output", "outputs", "生成结果", "生成文件", "history", "历史", "画廊"))
        has_delete = any(k in text for k in ("清理", "清空", "删除", "删掉", "全部删", "全删", "清除"))
        return has_target and has_delete
