"""信号规则引擎(说明书 §9)。
信号只入库 + 推送,不直接触发下单;buy/exit 生成 order_intent(pending)
等待 Telegram 人工确认(§17)。
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

import settings
from analysis import sentiment
from models import (IndicatorDaily, OrderIntent, PositionSnapshot, Signal,
                    WatchlistItem)

log = logging.getLogger(__name__)

RULES = [
    ("rsi_oversold",      lambda r: r.rsi < 30 and r.close > r.sma200,          "buy"),
    ("macd_cross_up",     lambda r: r.macd_cross == 1,                          "buy"),
    ("stop_signal",       lambda r: r.close < r.sma50 * 0.97,                   "exit"),
    ("news_shock",        lambda r: r.sent_avg <= -1.5 and r.news_cnt >= 3,     "alert"),
    # ★ 社区正向爆发:近 3 天正向帖 ≥ 5 且均分 ≥ 1.2
    ("community_bullish", lambda r: r.comm_pos_cnt >= 5 and r.comm_avg >= 1.2,  "alert"),
]


def _latest_indicators(db, symbol: str) -> IndicatorDaily | None:
    return db.execute(
        select(IndicatorDaily).where(IndicatorDaily.symbol == symbol)
        .order_by(IndicatorDaily.ts.desc()).limit(1)
    ).scalar_one_or_none()


def _has_position(db, t212_ticker: str | None) -> bool:
    if not t212_ticker:
        return False
    latest_ts = db.execute(select(func.max(PositionSnapshot.ts))).scalar()
    if not latest_ts:
        return False
    row = db.execute(select(PositionSnapshot).where(
        PositionSnapshot.ts == latest_ts,
        PositionSnapshot.ticker == t212_ticker)).scalar_one_or_none()
    return bool(row and (row.quantity or 0) > 0)


def _signal_exists_today(db, symbol: str, rule: str) -> bool:
    today = datetime.now(timezone.utc).date()
    n = db.execute(select(func.count()).select_from(Signal).where(
        Signal.symbol == symbol, Signal.rule == rule,
        func.date(Signal.ts) == today)).scalar()
    return (n or 0) > 0


def _pending_intent_exists(db, symbol: str, rule: str) -> bool:
    n = db.execute(select(func.count()).select_from(OrderIntent).where(
        OrderIntent.symbol == symbol, OrderIntent.rule == rule,
        OrderIntent.status == "pending")).scalar()
    return (n or 0) > 0


def evaluate(db) -> list[dict]:
    """对全部 watchlist 评估规则。返回待推送事件列表(由调用方 dispatch):
    [{event_type, symbol, group_id, payload}]
    """
    import config
    events = []
    for s in config.active_symbols(db):
        sym = s["symbol"]
        ind = _latest_indicators(db, sym)
        if not ind:
            continue
        agg = sentiment.symbol_aggregates(db, sym)
        r = SimpleNamespace(
            close=ind.close, rsi=ind.rsi, macd=ind.macd,
            macd_cross=ind.macd_cross or 0, sma50=ind.sma50,
            sma200=ind.sma200, **agg)

        for rule, fn, direction in RULES:
            try:
                hit = bool(fn(r))
            except TypeError:        # 指标为空(数据不足)
                hit = False
            if not hit or _signal_exists_today(db, sym, rule):
                continue
            # stop_signal 只对实际持仓有意义
            if rule == "stop_signal" and not _has_position(db, s["t212_ticker"]):
                continue

            strength = _strength(rule, r)
            details = {"rsi": r.rsi, "close": r.close, "sent_avg": r.sent_avg,
                       "news_cnt": r.news_cnt, "comm_avg": r.comm_avg,
                       "comm_pos_cnt": r.comm_pos_cnt}
            db.add(Signal(symbol=sym, rule=rule, direction=direction,
                          strength=strength, details=details, pushed=True))

            intent = None
            if direction in ("buy", "exit") \
                    and not _pending_intent_exists(db, sym, rule):
                value = min(settings.DEFAULT_ORDER_VALUE_EUR,
                            settings.RISK_MAX_ORDER_EUR)
                qty = round(value / r.close, 4) if r.close else 0
                intent = OrderIntent(
                    symbol=sym, t212_ticker=s["t212_ticker"],
                    side="buy" if direction == "buy" else "sell",
                    rule=rule, order_value_eur=value, quantity=qty,
                    price_at_signal=r.close,
                    expires_at=datetime.now(timezone.utc)
                    + timedelta(minutes=settings.INTENT_TTL_MINUTES),
                )
                db.add(intent)
                db.flush()

            event_type = "news_shock" if rule == "news_shock" else "signal"
            for gid in s["groups"]:
                if intent and not intent.group_id:
                    intent.group_id = gid
                events.append({
                    "event_type": event_type, "symbol": sym, "group_id": gid,
                    "payload": _payload(s, gid, rule, direction, r,
                                        strength, intent),
                })
    db.flush()
    log.info("signals evaluated: %d events", len(events))
    return events


def _strength(rule: str, r) -> float:
    try:
        if rule == "rsi_oversold":
            return round((30 - r.rsi) / 30, 2)
        if rule == "macd_cross_up":
            return 1.0
        if rule == "news_shock":
            return round(abs(r.sent_avg), 2)
        if rule == "community_bullish":
            return round(r.comm_avg, 2)
    except TypeError:
        pass
    return 1.0


def _payload(s, gid, rule, direction, r, strength, intent) -> dict:
    sym = s["symbol"]
    if rule == "news_shock":
        subject = f"⚠️ {sym} 新闻异动"
        body = (
            f"⚠️ <b>{sym} 新闻异动</b>\n"
            f"情绪骤降: {r.sent_avg:+.1f} (近 3 日均值, {r.news_cnt} 条)\n"
            f"社区: {r.comm_neg_cnt} 条看空帖\n"
            f"该标的属于组: {', '.join(s['groups'])}"
        )
    else:
        subject = f"📈 信号触发 · {sym} · {rule}"
        body = (
            f"📈 <b>信号触发 · {sym}</b>\n"
            f"组: {gid}\n"
            f"规则: {rule} · 方向: {direction} · 强度: {strength:.2f}\n"
            f"新闻情绪: {r.sent_avg:+.1f} · 社区情绪: {r.comm_avg:+.1f}\n"
        )
        if intent:
            body += (
                f"\n建议: {'买入' if intent.side == 'buy' else '卖出'} "
                f"€{intent.order_value_eur:.2f} ≈ {intent.quantity:.2f} 股 "
                f"@ {intent.price_at_signal:.2f}\n"
                f"<code>intent: {intent.id[:8]}…{intent.id[-4:]}</code> · "
                f"{settings.INTENT_TTL_MINUTES} 分钟内有效"
            )
    payload = {"subject": subject, "body_md": body,
               "body_html": body.replace("\n", "<br>")}
    if intent:
        payload["intent_id"] = intent.id
    return payload
