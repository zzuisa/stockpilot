"""Telegram 多 chat_id 推送 + 确认按钮回调(说明书 §11.2 / §17)。
模板统一用 HTML parse_mode(比 MarkdownV2 免转义,内容含财经符号更稳)。
"""
import asyncio
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import settings

log = logging.getLogger(__name__)

application: Application | None = None   # 由 start_bot() 填充
_bot: Bot | None = None
_bot_lock = asyncio.Lock()


async def _get_bot() -> Bot:
    global _bot
    if application is not None:
        return application.bot
    async with _bot_lock:
        if _bot is None:
            b = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            await b.initialize()
            _bot = b
    return _bot


class TelegramSender:
    async def send(self, chat_id: str, text: str, reply_markup=None):
        if not settings.telegram_enabled:
            raise RuntimeError("TELEGRAM_BOT_TOKEN 未配置")
        bot = await _get_bot()
        await bot.send_message(
            chat_id=chat_id,
            text=text[:4000],
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    async def send_signal_card(self, chat_id: str, text: str, intent_id: str):
        """带确认按钮的信号卡片 — 只发给管理员 chat(§17)"""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认下单",
                                 callback_data=f"confirm:{intent_id}"),
            InlineKeyboardButton("❌ 忽略",
                                 callback_data=f"skip:{intent_id}"),
        ]])
        await self.send(chat_id, text, reply_markup=kb)


# ─── 回调处理(确认下单的安全闸门) ───

async def _on_callback(update, context):
    q = update.callback_query
    uid = str(q.from_user.id) if q.from_user else ""
    # 安全:校验 user_id == ADMIN_USER_ID,防止群里其他人点(§17)
    if not settings.TELEGRAM_ADMIN_USER_ID \
            or uid != settings.TELEGRAM_ADMIN_USER_ID:
        await q.answer("无权操作", show_alert=True)
        return
    try:
        action, intent_id = q.data.split(":", 1)
    except ValueError:
        await q.answer("无效回调")
        return

    from trading import executor
    if action == "confirm":
        result = await asyncio.to_thread(
            executor.execute_intent, intent_id, uid)
    elif action == "skip":
        result = await asyncio.to_thread(
            executor.skip_intent, intent_id, uid)
    else:
        await q.answer("未知操作")
        return

    await q.answer(result["message"][:180])
    try:
        await q.edit_message_text(
            (q.message.text_html or q.message.text or "")
            + f"\n\n▶ <b>{result['status']}</b>: {result['message']}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:                       # 消息过旧等编辑失败,不影响结果
        pass


async def _on_ping(update, context):
    await update.message.reply_text("pong · StockPilot 在线")


def build_application() -> Application:
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("ping", _on_ping))
    app.add_handler(CallbackQueryHandler(_on_callback,
                                         pattern=r"^(confirm|skip):"))
    return app


async def start_bot():
    """polling 模式起轮询;webhook 模式只注册 webhook 地址(§17 webhook 回调)"""
    global application
    if not settings.telegram_enabled:
        log.info("Telegram 未配置,bot 不启动")
        return
    application = build_application()
    await application.initialize()
    await application.start()
    if settings.TELEGRAM_MODE == "webhook":
        if settings.TELEGRAM_WEBHOOK_SECRET:
            await application.bot.set_webhook(
                url=settings.WEBHOOK_PUBLIC_URL,
                secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
                allowed_updates=["message", "callback_query"],
            )
            log.info("telegram webhook set: %s", settings.WEBHOOK_PUBLIC_URL)
        else:
            log.warning("TELEGRAM_MODE=webhook 但未设置 TELEGRAM_WEBHOOK_SECRET")
    else:
        await application.bot.delete_webhook(drop_pending_updates=False)
        await application.updater.start_polling(
            allowed_updates=["message", "callback_query"])
        log.info("telegram polling started")


async def stop_bot():
    global application
    if application is None:
        return
    try:
        if application.updater and application.updater.running:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
    finally:
        application = None
