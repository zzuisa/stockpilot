"""路由引擎(说明书 §11.1):按 事件类型 + group + symbol 查 notify_routes,
分发到各通道;notify_log 记录 + 24h 防重发。
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import settings
from db import get_session
from models import NotifyLog, NotifyRoute

log = logging.getLogger(__name__)


def _payload_hash(recipient: str, payload: dict) -> str:
    raw = f"{recipient}:{json.dumps(payload, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class NotifyRouter:
    """根据事件类型 + group + symbol 查路由表,分发到各通道"""

    def __init__(self, telegram_sender, email_sender):
        self.tg = telegram_sender
        self.email = email_sender

    async def dispatch(self, event_type: str, symbol: str | None,
                       group_id: str, payload: dict):
        """
        event_type: 'daily_report' | 'signal' | 'news_shock'
        payload: {"subject","body_md","body_html"[, "intent_id"]}
        """
        routes = self._resolve_routes(event_type, symbol, group_id)
        if not routes:
            log.info("no routes: %s/%s/%s", event_type, group_id, symbol)
        for route in routes:
            h = _payload_hash(route.recipient, payload)
            if self._already_sent(h):
                self._log(route, event_type, symbol, "skipped",
                          "duplicate within 24h", h)
                continue
            try:
                if route.channel == "telegram":
                    intent_id = payload.get("intent_id")
                    # §17:只有管理员主 chat 收到带确认按钮的卡片,群组只读
                    if intent_id and route.recipient == settings.TELEGRAM_CHAT_ID:
                        await self.tg.send_signal_card(
                            route.recipient, payload["body_md"], intent_id)
                    else:
                        await self.tg.send(route.recipient, payload["body_md"])
                elif route.channel == "email":
                    await self.email.send(
                        to=route.recipient,
                        subject=payload.get("subject", "StockPilot 通知"),
                        body_html=payload.get("body_html", ""),
                    )
                else:
                    self._log(route, event_type, symbol, "skipped",
                              f"unknown channel {route.channel}", h)
                    continue
                self._log(route, event_type, symbol, "sent", None, h)
            except Exception as e:
                log.warning("notify failed %s→%s: %s",
                            route.channel, route.recipient, e)
                self._log(route, event_type, symbol, "failed", str(e)[:500], h)

    async def dispatch_events(self, events: list[dict]):
        for e in events:
            await self.dispatch(e["event_type"], e.get("symbol"),
                                e["group_id"], e["payload"])

    def _resolve_routes(self, event_type, symbol, group_id):
        """查 notify_routes:symbol 级优先,同 channel+recipient 去重"""
        with get_session() as s:
            q = select(NotifyRoute).where(
                NotifyRoute.group_id == group_id,
                NotifyRoute.active.is_(True),
                NotifyRoute.event_types.contains([event_type]),
            )
            if symbol:
                q = q.where((NotifyRoute.symbol == symbol)
                            | (NotifyRoute.symbol.is_(None)))
            else:
                q = q.where(NotifyRoute.symbol.is_(None))
            routes = s.execute(q).scalars().all()
            seen, result = set(), []
            for r in sorted(routes, key=lambda x: (x.symbol is None)):
                key = (r.channel, r.recipient)
                if key not in seen:
                    seen.add(key)
                    result.append(r)
            return result

    def _log(self, route, event_type, symbol, status, error=None,
             payload_hash=None):
        with get_session() as s:
            s.add(NotifyLog(
                event_type=event_type, group_id=route.group_id,
                symbol=symbol or route.symbol, channel=route.channel,
                recipient=route.recipient, status=status,
                error_msg=error, payload_hash=payload_hash,
            ))

    def _already_sent(self, payload_hash: str) -> bool:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        with get_session() as s:
            row = s.execute(select(NotifyLog.id).where(
                NotifyLog.payload_hash == payload_hash,
                NotifyLog.status == "sent",
                NotifyLog.ts >= since).limit(1)).first()
            return row is not None
