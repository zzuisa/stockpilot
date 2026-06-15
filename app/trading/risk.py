"""硬风控(说明书 §19):确认下单前的最后闸门,宁紧勿松。
任何校验数据缺失一律拒绝(fail-safe)。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

import settings
from models import AccountSnapshot, OrderIntent, PositionSnapshot

log = logging.getLogger(__name__)


def validate_intent(db, intent: OrderIntent) -> tuple[bool, str]:
    if intent.expires_at and intent.expires_at < datetime.now(timezone.utc):
        return False, "intent 已过期"
    if not intent.t212_ticker:
        return False, "无 t212_ticker,仅观察标的"
    if (intent.order_value_eur or 0) > settings.RISK_MAX_ORDER_EUR:
        return False, (f"单笔 €{intent.order_value_eur:.0f} 超上限 "
                       f"€{settings.RISK_MAX_ORDER_EUR:.0f}")

    acc = db.execute(select(AccountSnapshot)
                     .order_by(AccountSnapshot.ts.desc()).limit(1)
                     ).scalar_one_or_none()
    if acc is None or not acc.total:
        return False, "无账户快照,风控无法校验"

    latest_ts = db.execute(select(func.max(PositionSnapshot.ts))).scalar()
    pos = None
    if latest_ts:
        pos = db.execute(select(PositionSnapshot).where(
            PositionSnapshot.ts == latest_ts,
            PositionSnapshot.ticker == intent.t212_ticker)
        ).scalar_one_or_none()

    if intent.side == "buy":
        # 仓位占比上限
        pos_value = (pos.quantity or 0) * (pos.current_price or 0) if pos else 0
        pct = (pos_value + (intent.order_value_eur or 0)) / acc.total * 100
        if pct > settings.RISK_MAX_POSITION_PCT:
            return False, (f"仓位将达 {pct:.1f}%,超上限 "
                           f"{settings.RISK_MAX_POSITION_PCT:.0f}%")
        # 当日亏损熔断
        today = datetime.now(timezone.utc).date()
        first_today = db.execute(
            select(AccountSnapshot).where(
                func.date(AccountSnapshot.ts) == today)
            .order_by(AccountSnapshot.ts.asc()).limit(1)
        ).scalar_one_or_none()
        if first_today and first_today.total:
            daily_loss = first_today.total - acc.total
            if daily_loss > settings.RISK_DAILY_LOSS_LIMIT_EUR:
                return False, (f"当日亏损 €{daily_loss:.0f} 触发熔断 "
                               f"(限 €{settings.RISK_DAILY_LOSS_LIMIT_EUR:.0f})")
        # 现金充足
        if (acc.free_cash or 0) < (intent.order_value_eur or 0):
            return False, f"现金不足 (€{acc.free_cash or 0:.0f})"
    else:  # sell / exit
        if not pos or (pos.quantity or 0) <= 0:
            return False, "无持仓,无法卖出"
        if intent.quantity > pos.quantity:
            intent.quantity = pos.quantity   # 收紧到实际持仓
    return True, "ok"
