"""Telegram webhook 回调(说明书 §3 api/webhook.py)。
默认 TELEGRAM_MODE=polling 时此端点闲置;切 webhook 模式后,
nginx 将 /stockpilot/webhook/telegram 免 basic-auth 转发到这里,
以 X-Telegram-Bot-Api-Secret-Token 校验来源。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from telegram import Update

import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    if settings.TELEGRAM_MODE != "webhook":
        raise HTTPException(404, "webhook 模式未启用 (TELEGRAM_MODE=polling)")
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not settings.TELEGRAM_WEBHOOK_SECRET \
            or secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "secret token 校验失败")

    from notify import telegram as tg
    if tg.application is None:
        raise HTTPException(503, "bot 未启动")
    data = await request.json()
    update = Update.de_json(data, tg.application.bot)
    await tg.application.update_queue.put(update)
    return {"ok": True}
