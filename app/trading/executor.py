"""确认下单执行器(说明书 §17):
点击确认 → 风控 → 幂等检查 → market_order → 回执。
状态机: pending → confirmed → executed | failed;或 skipped / expired / rejected
"""
import logging
from datetime import datetime, timezone

import settings
from db import get_session
from models import OrderIntent
from trading import risk

log = logging.getLogger(__name__)


def execute_intent(intent_id: str, confirmed_by: str) -> dict:
    with get_session() as db:
        intent = db.get(OrderIntent, intent_id, with_for_update=True)
        if intent is None:
            return {"ok": False, "status": "not_found",
                    "message": f"intent {intent_id[:8]} 不存在"}
        # 幂等:只有 pending 可被确认,重复点击直接返回当前状态
        if intent.status != "pending":
            return {"ok": False, "status": intent.status,
                    "message": f"已是 {intent.status} 状态,忽略重复操作"}
        if intent.expires_at and intent.expires_at < datetime.now(timezone.utc):
            intent.status = "expired"
            intent.status_reason = "确认时已超时"
            return {"ok": False, "status": "expired",
                    "message": "已超过 30 分钟有效期"}

        ok, reason = risk.validate_intent(db, intent)
        if not ok:
            intent.status = "rejected"
            intent.status_reason = reason
            log.warning("intent %s rejected: %s", intent_id[:8], reason)
            return {"ok": False, "status": "rejected",
                    "message": f"风控拒绝: {reason}"}

        intent.status = "confirmed"
        intent.confirmed_by = confirmed_by
        db.flush()

        if not settings.t212_enabled:
            intent.status = "failed"
            intent.status_reason = "T212_API_KEY 未配置"
            return {"ok": False, "status": "failed",
                    "message": "T212 未配置,无法下单"}
        try:
            from t212.client import T212
            qty = intent.quantity if intent.side == "buy" else -intent.quantity
            res = T212().market_order(intent.t212_ticker, qty)
            intent.status = "executed"
            intent.executed_order_id = str(res.get("id", ""))
            log.info("intent %s executed: order %s",
                     intent_id[:8], intent.executed_order_id)
            return {"ok": True, "status": "executed",
                    "message": (f"{intent.side} {intent.symbol} "
                                f"{abs(qty):.4f} 股已提交 "
                                f"(订单 {intent.executed_order_id})")}
        except Exception as e:
            intent.status = "failed"
            intent.status_reason = str(e)[:500]
            log.exception("intent %s 下单失败", intent_id[:8])
            return {"ok": False, "status": "failed",
                    "message": f"下单失败: {e}"}


def skip_intent(intent_id: str, by: str) -> dict:
    with get_session() as db:
        intent = db.get(OrderIntent, intent_id, with_for_update=True)
        if intent is None:
            return {"ok": False, "status": "not_found", "message": "不存在"}
        if intent.status != "pending":
            return {"ok": False, "status": intent.status,
                    "message": f"已是 {intent.status} 状态"}
        intent.status = "skipped"
        intent.confirmed_by = by
        return {"ok": True, "status": "skipped", "message": "已忽略"}


def expire_stale(db) -> int:
    """超时 30min → expired(§17),由调度器每 5 分钟扫一次"""
    rows = db.query(OrderIntent).filter(
        OrderIntent.status == "pending",
        OrderIntent.expires_at < datetime.now(timezone.utc)).all()
    for r in rows:
        r.status = "expired"
        r.status_reason = "30 分钟未确认"
    return len(rows)
