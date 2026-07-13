"""OrderIntent 的网页内确认/忽略端点（补齐此前只能经 Telegram 确认的缺口）。

Agent（交互式或托管超阈值升级）产出 pending intent 后，用户可在网页上确认或忽略。
复用 trading/executor 的状态机与风控闸门——与 Telegram 按钮走的是同一执行路径。
"""
import logging

from fastapi import APIRouter, Query
from sqlalchemy import select

from db import get_session
from models import OrderIntent
from trading import executor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/intents", tags=["intents"])


@router.get("")
async def list_intents(status: str | None = Query(None),
                       limit: int = Query(50, ge=1, le=200)):
    with get_session() as db:
        q = select(OrderIntent).order_by(OrderIntent.created_at.desc()).limit(limit)
        if status:
            q = q.where(OrderIntent.status == status)
        rows = db.execute(q).scalars().all()
        return [{"id": r.id, "symbol": r.symbol, "side": r.side, "rule": r.rule,
                 "quantity": r.quantity, "order_value_eur": r.order_value_eur,
                 "price_at_signal": r.price_at_signal, "status": r.status,
                 "status_reason": r.status_reason,
                 "created_at": r.created_at.isoformat() if r.created_at else None,
                 "expires_at": r.expires_at.isoformat() if r.expires_at else None}
                for r in rows]


@router.post("/{intent_id}/confirm")
async def confirm(intent_id: str):
    """网页确认下单 → executor.execute_intent（含风控 + T212 下单）。"""
    import asyncio
    res = await asyncio.to_thread(executor.execute_intent, intent_id, "web")
    return res


@router.post("/{intent_id}/skip")
async def skip(intent_id: str):
    import asyncio
    res = await asyncio.to_thread(executor.skip_intent, intent_id, "web")
    return res
