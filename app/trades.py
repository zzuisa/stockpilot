"""统一交易历史记录助手。

应用经手的每一笔下单（手动 UI + 量化循环）都调用 record_trade 落库到
trade_log 表，供交易历史页与概览大屏使用。写入失败只告警，绝不影响下单主流程。
"""
import logging

import settings
from db import get_session
from models import TradeLog

log = logging.getLogger(__name__)


def record_trade(*, source: str, side: str, symbol: str | None = None,
                 t212_ticker: str | None = None, order_type: str | None = None,
                 quantity: float | None = None, price: float | None = None,
                 value_eur: float | None = None, currency: str | None = None,
                 pnl: float | None = None,
                 reason: str | None = None,
                 status: str = "submitted", order_id: str | None = None,
                 detail: dict | None = None) -> None:
    """写一条交易历史。symbol 缺省时由 t212_ticker 推断（NVDA_US_EQ → NVDA）。"""
    if not symbol and t212_ticker:
        symbol = t212_ticker.split("_")[0].upper()
    try:
        with get_session() as s:
            s.add(TradeLog(
                source=source, symbol=symbol, t212_ticker=t212_ticker,
                side=side, order_type=order_type, quantity=quantity,
                price=price, value_eur=value_eur, currency=currency, pnl=pnl,
                reason=reason,
                status=status, order_id=str(order_id) if order_id else None,
                env=settings.T212_ENV or "demo", detail=detail,
            ))
    except Exception as e:
        log.warning("record_trade failed: %s", e)
