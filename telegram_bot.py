import os, sys, json, threading, asyncio, logging, time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, tester, token, allowed_users=None, proxy_url=None, connect_timeout=20, read_timeout=30, write_timeout=30):
        self.tester = tester
        self.token = token
        self.allowed_users = allowed_users or []
        self.proxy_url = proxy_url
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.application = None
        self._loop = None
        self._pending_chains = {}
        self._pending_choices = {}
        self._stop_event = threading.Event()
        self.last_error = ""
        self._chat_locks = {}

    def _get_lock(self, chat_id):
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = threading.Lock()
        return self._chat_locks[chat_id]

    def _check_user(self, user_id):
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _run_async(self):
        builder = (
            Application.builder()
            .token(self.token)
            .connect_timeout(self.connect_timeout)
            .read_timeout(self.read_timeout)
            .write_timeout(self.write_timeout)
            .get_updates_connect_timeout(self.connect_timeout)
            .get_updates_read_timeout(self.read_timeout)
            .get_updates_write_timeout(self.write_timeout)
        )
        if self.proxy_url:
            builder = builder.proxy(self.proxy_url).get_updates_proxy(self.proxy_url)
        app = builder.build()
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("new", self._cmd_new))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_error_handler(self._on_error)
        self.application = app
        self._loop = asyncio.get_running_loop()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            error_callback=self._on_polling_error,
        )
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    def run(self):
        self._stop_event.clear()
        try:
            asyncio.run(self._run_async())
        except Exception as e:
            self.last_error = str(e)
            logger.error("Telegram bot stopped: %s", e)

    def stop(self):
        self._stop_event.set()
        if self.application and self._loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.application.stop(), self._loop
                )
                future.result(timeout=5)
            except Exception:
                pass

    async def _on_error(self, update, context):
        err = context.error
        self.last_error = str(err)
        if isinstance(err, NetworkError):
            logger.warning("Telegram network error: %s", err)
            return
        logger.error("Telegram handler error", exc_info=(type(err), err, err.__traceback__))

    def _on_polling_error(self, err):
        self.last_error = str(err)
        if isinstance(err, NetworkError):
            logger.warning("Telegram polling network error: %s", err)
            return
        logger.error("Telegram polling error: %s", err)

    async def _cmd_start(self, update, context):
        if not self._check_user(update.effective_user.id):
            await update.message.reply_text("未授权用户")
            return
        await update.message.reply_text(
            "我是苏丹娜，你的画图助手~\n\n"
            "直接发文字描述想画什么，我会帮你分析标签并生成图片。\n"
            "命令：\n"
            "/new - 新对话\n"
            "/help - 帮助"
        )

    async def _cmd_help(self, update, context):
        if not self._check_user(update.effective_user.id):
            return
        await update.message.reply_text(
            "用自然语言告诉我想画什么就行。\n\n"
            "例子:\n"
            "  - 画一个白发异色瞳的猫娘\n"
            "  - 用 single 模式画 5 张猫娘\n"
            "  - 调用 爱音西娅noobXL-vpred 画一个粉色长发少女\n\n"
            "LLM 会自动分析你的描述并返回可执行的操作。"
        )

    async def _cmd_new(self, update, context):
        if not self._check_user(update.effective_user.id):
            return
        self.tester._session_create(f"TG-{update.effective_user.id}")
        await update.message.reply_text("已开始新对话")

    async def _on_message(self, update, context):
        if not self._check_user(update.effective_user.id):
            return
        chat_id = update.effective_chat.id
        with self._get_lock(chat_id):
            user_text = update.message.text
            msg = await update.message.reply_text("正在思考...")
            try:
                result = self.tester._agent_process(user_text, source="telegram", user_id=update.effective_user.id)
                reply = result.get("reply", "")
                choices = result.get("choices") or []
                chain = self.tester._extract_chain(result)
            except Exception as e:
                await msg.edit_text(f"处理失败: {e}")
                return

            if choices:
                _, session = self.tester._session_current()
                self.tester.agent.state.save_choices(session, user_text, choices)
                self.tester._save_sessions()
                full_text = (reply or "我理解你可能有几种处理方式：") + "\n\n" + self._format_choices(choices)
                await msg.edit_text(full_text, reply_markup=self._build_choices_keyboard(choices))
                return

            if not chain:
                await msg.edit_text(reply)
                return

            if self._is_generation_research_chain(chain):
                await msg.edit_text((reply or "正在查询角色资料...") + "\n\n查询完成后我会给你可选构图。")
                thread = threading.Thread(
                    target=self._execute_research_then_reply_sync,
                    args=(chat_id, chain, user_text),
                    daemon=True,
                )
                thread.start()
                return

            chain_text = self._format_chain(chain)
            full_text = f"{reply}\n\n{chain_text}" if reply else chain_text
            keyboard = self._build_keyboard(chain)
            await msg.edit_text(full_text, reply_markup=keyboard)

    def _format_chain(self, chain):
        lines = []
        for i, step in enumerate(chain):
            desc = self.tester._step_desc(step["action"], step.get("params", {}))
            lines.append(f"{i+1}. {desc}")
        return "\n".join(lines)

    def _build_keyboard(self, chain):
        key_id = str(time.time_ns())
        self._pending_chains[key_id] = chain
        lines = []
        if len(chain) == 1:
            lines.append([
                InlineKeyboardButton("执行", callback_data=f"exec|{key_id}"),
                InlineKeyboardButton("修改", callback_data=f"edit|{key_id}"),
                InlineKeyboardButton("取消", callback_data=f"cancel|{key_id}"),
            ])
        else:
            lines.append([
                InlineKeyboardButton("执行全部", callback_data=f"exec|{key_id}"),
                InlineKeyboardButton("取消", callback_data=f"cancel|{key_id}"),
            ])
        return InlineKeyboardMarkup(lines)

    def _format_choices(self, choices):
        lines = []
        for i, choice in enumerate(choices[:5], 1):
            label = choice.get("label") or f"选项 {i}"
            desc = choice.get("description") or ""
            lines.append(f"{i}. {label}" + (f" — {desc}" if desc else ""))
        return "\n".join(lines)

    def _build_choices_keyboard(self, choices):
        key_id = str(time.time_ns())
        self._pending_choices[key_id] = choices[:5]
        rows = []
        for i, choice in enumerate(choices[:5]):
            label = choice.get("label") or f"选项 {i+1}"
            rows.append([InlineKeyboardButton(label[:24], callback_data=f"choice|{key_id}|{i}")])
        rows.append([InlineKeyboardButton("取消", callback_data=f"choice_cancel|{key_id}|0")])
        return InlineKeyboardMarkup(rows)

    async def _on_callback(self, update, context):
        query = update.callback_query
        await query.answer()
        data = query.data
        chat_id = query.message.chat_id
        with self._get_lock(chat_id):
            parts = data.split("|", 1)
            action = parts[0]
            key_id = parts[1] if len(parts) > 1 else ""

            if action in ("choice", "choice_cancel"):
                sub = data.split("|")
                choice_key = sub[1] if len(sub) > 1 else ""
                choice_idx = int(sub[2]) if len(sub) > 2 and sub[2].isdigit() else -1
                choices = self._pending_choices.pop(choice_key, None)
                await query.edit_message_reply_markup(reply_markup=None)
                if action == "choice_cancel" or not choices or choice_idx < 0 or choice_idx >= len(choices):
                    _, session = self.tester._session_current()
                    self.tester.agent.state.mark_choice(session, cancelled=True)
                    self.tester._save_sessions()
                    await query.edit_message_text((query.message.text or "") + "\n\n[已取消]")
                    return
                choice = choices[choice_idx]
                chain = choice.get("chain") or []
                if not chain:
                    await query.edit_message_text((query.message.text or "") + "\n\n[选项无可执行步骤]")
                    return
                try:
                    _, session = self.tester._session_current()
                    self.tester.agent.state.mark_choice(session, index=choice_idx, cancelled=False)
                    self.tester._save_sessions()
                except Exception:
                    pass
                await query.edit_message_text(
                    (query.message.text or "").split("\n\n")[0]
                    + f"\n\n已选择：{choice.get('label', f'选项 {choice_idx + 1}')}\n任务已提交，执行中..."
                )
                thread = threading.Thread(
                    target=self._execute_chain_sync,
                    args=(chat_id, chain),
                    daemon=True,
                )
                thread.start()
                return

            chain = self._pending_chains.pop(key_id, None)

            if action == "cancel" or not chain:
                await query.edit_message_reply_markup(reply_markup=None)
                if chain is None:
                    await query.edit_message_text(
                        (query.message.text or "") + "\n\n[已过期]",
                    )
                return

            if action == "edit":
                await query.edit_message_text(
                    (query.message.text or "").split("\n\n")[0]
                    + "\n\n请回复修改后的描述:"
                )
                context.user_data["editing_key"] = key_id
                return

            if action == "exec":
                await query.edit_message_reply_markup(reply_markup=None)
                await query.edit_message_text(
                    (query.message.text or "").split("\n\n")[0]
                    + "\n\n任务已提交，执行中..."
                )
                thread = threading.Thread(
                    target=self._execute_chain_sync,
                    args=(chat_id, chain),
                    daemon=True,
                )
                thread.start()

    def _execute_chain_sync(self, chat_id, chain):
        loop = self._loop
        bot = self.application.bot
        status_msg_id = None

        async def _send(text):
            nonlocal status_msg_id
            if status_msg_id:
                try:
                    m = await bot.edit_message_text(text, chat_id=chat_id, message_id=status_msg_id)
                    status_msg_id = m.message_id
                except Exception:
                    m = await bot.send_message(chat_id, text)
                    status_msg_id = m.message_id
            else:
                m = await bot.send_message(chat_id, text)
                status_msg_id = m.message_id

        async def _send_result(batch_name):
            output_dir = Path(self.tester.script_dir) / self.tester.config["output"]["base_dir"]
            batch_path = output_dir / batch_name
            if not batch_path.exists():
                await _send(f"完成，批次: {batch_name}")
                return
            pngs = sorted(batch_path.glob("*.png"))
            if not pngs:
                await _send(f"完成，批次: {batch_name}（无图片）")
                return
            for p in pngs:
                try:
                    with open(p, "rb") as f:
                        caption = p.stem[:100]
                        await bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            filename=p.name,
                            caption=caption,
                            read_timeout=60,
                            write_timeout=60,
                        )
                except Exception as e:
                    await _send(f"发送图片失败: {p.name} - {e}")

        def _run_coro(coro):
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=120)

        tool_results = []
        try:
            _, session = self.tester._session_current()
            for i, step in enumerate(chain):
                desc = self.tester._step_desc(step["action"], step.get("params", {}))
                _run_coro(_send(f"[{i+1}/{len(chain)}] {desc}"))
                with self._get_lock(chat_id):
                    action_result = self.tester._execute_action(step["action"], step.get("params", {}))
                if not action_result.get("ok", True):
                    err = action_result.get("error") or action_result.get("summary") or "未知错误"
                    tool_results.append(f"[Tool Result] {step['action']}: 失败 - {err}")
                    _run_coro(_send(f"  ✗ 失败: {err}"))
                else:
                    summary = action_result.get("summary", "")
                    tool_results.append(f"[Tool Result] {step['action']}: {summary or '成功'}")
                    if summary:
                        _run_coro(_send(f"  ✓ {summary}"))
                with self._get_lock(chat_id):
                    try:
                        if step.get("action") == "dream" and isinstance(action_result, dict):
                            gen = {"description": step.get("params", {}).get("description", ""),
                                   "prompt": action_result.get("prompt", ""),
                                   "params": step.get("params", {}),
                                   "run": action_result.get("run")}
                            self.tester.agent.state.save_generation(session, gen)
                    except Exception:
                        pass
                    try:
                        self.tester._inject_action_result(step, action_result)
                    except Exception:
                        pass
                    self.tester._save_sessions()

            last_run = getattr(self.tester, "last_run_dir", None)
            if last_run:
                _run_coro(_send_result(last_run.name))
            else:
                _run_coro(_send("执行完毕"))

        except Exception as e:
            tool_results.append(f"[Tool Result] 异常: {e}")
            try:
                _run_coro(_send(f"执行失败: {e}"))
            except Exception:
                pass
        finally:
            pass

    def _execute_research_then_reply_sync(self, chat_id, chain, original_text):
        loop = self._loop
        bot = self.application.bot

        def _run_coro(coro):
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=120)

        try:
            with self._get_lock(chat_id):
                for step in chain:
                    action_result = self.tester._execute_action(step["action"], step.get("params", {}))
                    self.tester._inject_action_result(step, action_result)
                    if not action_result.get("ok", True):
                        err = action_result.get("error") or action_result.get("summary") or "未知错误"
                        _run_coro(bot.send_message(chat_id, f"角色资料查询失败: {err}"))
                        return
                result = self.tester._agent_process(
                    "继续。基于上一步的工具输出决定下一步。",
                    source="tool_result",
                )
            reply = result.get("reply", "") if isinstance(result, dict) else ""
            choices = result.get("choices") if isinstance(result, dict) else []
            chain2 = self.tester._extract_chain(result) if isinstance(result, dict) else []
            if choices:
                _, session = self.tester._session_current()
                self.tester.agent.state.save_choices(session, original_text, choices)
                self.tester._save_sessions()
                full_text = (reply or "我查到资料后整理了几个方向：") + "\n\n" + self._format_choices(choices)
                _run_coro(bot.send_message(chat_id, full_text, reply_markup=self._build_choices_keyboard(choices)))
            elif chain2:
                full_text = (reply + "\n\n" if reply else "") + self._format_chain(chain2)
                _run_coro(bot.send_message(chat_id, full_text, reply_markup=self._build_keyboard(chain2)))
            else:
                _run_coro(bot.send_message(chat_id, reply or "角色资料查询完成，但没有得到后续方案。"))
        except Exception as e:
            try:
                _run_coro(bot.send_message(chat_id, f"处理失败: {e}"))
            except Exception:
                pass

    def _is_generation_research_chain(self, chain):
        if len(chain or []) != 1:
            return False
        if chain[0].get("action") not in ("tagsite", "tags"):
            return False
        _, session = self.tester._session_current()
        task = (session.get("conversation_state") or {}).get("active_task") or {}
        return task.get("type") == "generation" and task.get("status") == "researching"
