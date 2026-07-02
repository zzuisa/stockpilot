"""T212 账户同步(说明书 §6):
- 每 30 分钟:positions + cash 快照
- 每天收盘后:订单/分红历史增量
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

import settings
from models import (AccountSnapshot, PositionSnapshot, T212Dividend, T212Order)
from t212.client import T212

log = logging.getLogger(__name__)


def _parse_ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def sync_account_snapshot(db):
    """positions + cash → 快照表"""
    if not settings.t212_enabled:
        log.info("T212 未配置,跳过账户同步")
        return {"skipped": True}
    client = T212()
    now = datetime.now(timezone.utc)

    raw_positions = client.positions() or []
    # T212 API v0 returns nested structure:
    # {"instrument": {"ticker": ...}, "walletImpact": {...}, "averagePricePaid": ...}
    raw_list = raw_positions if isinstance(raw_positions, list) else raw_positions.get("items", [])
    positions = []
    for p in raw_list:
        inst = p.get("instrument") or {}
        wi = p.get("walletImpact") or {}
        ticker = inst.get("ticker") or p.get("ticker")
        if not ticker:
            log.warning("positions_snapshot: skipping row with no ticker: %s", p)
            continue
        db.add(PositionSnapshot(
            ts=now,
            ticker=ticker,
            quantity=p.get("quantity"),
            average_price=p.get("averagePricePaid") or p.get("averagePrice"),
            current_price=p.get("currentPrice"),
            ppl=wi.get("unrealizedProfitLoss") or p.get("ppl"),
            fx_ppl=wi.get("fxImpact") or p.get("fxPpl"),
        ))
        positions.append(ticker)

    cash = client.cash() or {}
    db.add(AccountSnapshot(
        ts=now,
        free_cash=cash.get("free"),
        invested=cash.get("invested"),
        total=cash.get("total"),
        ppl=cash.get("ppl"),
        result=cash.get("result"),
    ))
    return {"positions": len(positions), "total": cash.get("total")}


def sync_history(db):
    """订单 + 分红历史,以 API 主键 upsert 实现增量（按活跃账户）。

    T212 订单历史为嵌套结构 {order:{...}, fill:{...}}：
      order: id/side(BUY|SELL)/ticker/type/status/filledQuantity/createdAt
      fill:  price(真实成交价)/quantity/filledAt/walletImpact.netValue(账户币种净额)
    """
    acct_id = None
    try:
        from t212.account_cache import get_client, get_active
        client = get_client()
        a = get_active()
        acct_id = a["id"] if a else None
    except Exception:
        if not settings.t212_enabled:
            log.info("T212 未配置,跳过历史同步")
            return {"skipped": True}
        client = T212()

    orders = (client.order_history(limit=50) or {}).get("items", [])
    synced = 0
    for o in orders:
        order = o.get("order") or {}
        fill = o.get("fill") or {}
        oid = order.get("id") or o.get("id")          # 兼容旧扁平 schema
        if oid is None:
            continue
        inst = order.get("instrument") or {}
        wi = fill.get("walletImpact") or {}
        vals = dict(
            id=oid,
            ticker=order.get("ticker") or inst.get("ticker") or o.get("ticker"),
            side=order.get("side"),
            type=order.get("type") or o.get("type"),
            status=order.get("status") or o.get("status"),
            filled_quantity=order.get("filledQuantity") or fill.get("quantity")
            or o.get("filledQuantity"),
            filled_value=wi.get("netValue") if wi.get("netValue") is not None
            else o.get("filledValue"),
            fill_price=fill.get("price") if fill.get("price") is not None
            else o.get("fillPrice"),
            date_created=_parse_ts(order.get("createdAt") or o.get("dateCreated")),
            filled_at=_parse_ts(fill.get("filledAt") or order.get("createdAt")),
            account_id=acct_id,
            raw=o,
        )
        stmt = pg_insert(T212Order).values(**vals).on_conflict_do_update(
            index_elements=["id"],
            set_={k: v for k, v in vals.items() if k != "id"},
        )
        db.execute(stmt)
        synced += 1

    dividends = (client.dividends(limit=50) or {}).get("items", [])
    for d in dividends:
        ref = d.get("reference") or d.get("ticker", "") + str(d.get("paidOn", ""))
        stmt = pg_insert(T212Dividend).values(
            reference=str(ref),
            ticker=d.get("ticker"),
            amount=d.get("amount"),
            amount_in_euro=d.get("amountInEuro"),
            paid_on=_parse_ts(d.get("paidOn")),
        ).on_conflict_do_nothing(index_elements=["reference"])
        db.execute(stmt)

    return {"orders": synced, "dividends": len(dividends)}
