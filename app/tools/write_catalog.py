"""写类能力工具（Phase 5）——**全部经架构护栏**：

- `create_order_intent`：只建 pending `OrderIntent` 并跑 `risk.validate_intent` 预检，
  **绝不调裸下单端点**。执行仍需人工确认（网页/Telegram）或托管在预算内自执行。
- `adjust_strategy`：把新参数并入现有 `QuantStrategy.params`，经唯一入口 `quant.start` 热切换。

两者 `risk` 非 read、`confirm_required=True`；默认不经 MCP 暴露（除非设置里显式开启）。
导入本模块即注册（`main.py` 里在能力总线装配后 import 一次）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import settings
from tools.registry import Tool, register

log = logging.getLogger(__name__)


def _sym(s: str) -> str:
    return s.split("_")[0].upper()


def _resolve_ticker(symbol: str) -> str | None:
    """从既有策略行或 watchlist 配置解析 t212_ticker。"""
    sym = _sym(symbol)
    try:
        from db import get_session
        from models import QuantStrategy
        import config
        with get_session() as db:
            row = db.get(QuantStrategy, sym)
            if row and row.t212_ticker:
                return row.t212_ticker
            for d in config.active_symbols(db):
                if d["symbol"] == sym:
                    return d.get("t212_ticker")
    except Exception as e:                       # noqa: BLE001
        log.warning("_resolve_ticker %s 失败: %s", sym, e)
    return None


def create_order_intent(symbol: str, side: str, value_eur: float | None = None,
                        quantity: float | None = None, rule: str = "agent") -> dict:
    """建 pending OrderIntent（经风控预检）。返回 {ok, intent_id, status, message}。"""
    from db import get_session
    from models import OrderIntent
    from trading import risk

    sym = _sym(symbol)
    if side not in ("buy", "sell"):
        return {"ok": False, "message": "side 须为 buy/sell"}
    ticker = _resolve_ticker(sym)
    if not ticker:
        return {"ok": False, "message": f"无法解析 {sym} 的 t212_ticker（不在自选/无策略）"}

    # 定价与股数：给了 value 未给 qty → 用市场快照现价换算
    price = None
    try:
        from analysis import market_data as md
        price = (md.build_market_data(sym).get("quote") or {}).get("live_price")
    except Exception:
        pass
    val = float(value_eur) if value_eur else min(
        settings.DEFAULT_ORDER_VALUE_EUR, settings.RISK_MAX_ORDER_EUR)
    qty = float(quantity) if quantity else (round(val / price, 4) if price else 0)
    if qty <= 0:
        return {"ok": False, "message": "股数为 0（缺现价或金额），未建单"}

    with get_session() as db:
        intent = OrderIntent(
            symbol=sym, t212_ticker=ticker, side=side, rule=rule,
            order_value_eur=val, quantity=qty, price_at_signal=price,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=settings.INTENT_TTL_MINUTES))
        db.add(intent)
        db.flush()
        ok, reason = risk.validate_intent(db, intent)
        if not ok:
            intent.status = "rejected"
            intent.status_reason = reason
            return {"ok": False, "intent_id": intent.id, "status": "rejected",
                    "message": f"风控拒绝：{reason}"}
        iid = intent.id
    return {"ok": True, "intent_id": iid, "status": "pending",
            "message": f"已建待确认单：{side} {sym} {qty} 股（≈€{val}）。等待确认/托管执行。"}


def adjust_strategy(symbol: str, params: dict) -> dict:
    """把 params 并入现有策略并经 quant.start 热切换。返回新策略 status。"""
    from db import get_session
    from models import QuantStrategy
    import quant

    sym = _sym(symbol)
    from analysis import appsettings
    if appsettings.kill_switch():
        return {"ok": False, "message": "kill-switch 已开启，禁止改策略"}
    ticker = _resolve_ticker(sym)
    if not ticker:
        return {"ok": False, "message": f"无法解析 {sym} 的 t212_ticker"}
    with get_session() as db:
        row = db.get(QuantStrategy, sym)
        base = dict(row.params) if row and row.params else dict(quant.DEFAULT_PARAMS)
    merged = {**base, **(params or {})}
    try:
        from t212.account_cache import get_active
        acct = get_active()
    except Exception:
        acct = None
    if not acct:
        return {"ok": False, "message": "未配置 T212 账户，无法启动/调整策略"}
    runner = quant.start(sym, ticker, merged, account_id=acct["id"],
                         api_key=acct["api_key"], api_secret=acct.get("api_secret"),
                         env=acct["env"])
    log.info("adjust_strategy %s applied: %s", sym, list((params or {}).keys()))
    return {"ok": True, "message": f"{sym} 策略已按新参数热切换", "status": runner.status()}


def _register() -> None:
    from tools.registry import REGISTRY
    tools = [
        Tool("create_order_intent",
             "建一张待确认订单意向(pending OrderIntent)并跑风控预检。只建单不执行；"
             "执行需人工确认或托管在预算内自动执行。side=buy/sell；给 value_eur 或 quantity 之一。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "side": {"type": "string", "enum": ["buy", "sell"]},
                             "value_eur": {"type": "number", "description": "下单金额(欧元)"},
                             "quantity": {"type": "number", "description": "股数(与 value 二选一)"},
                             "rule": {"type": "string", "default": "agent"}},
              "required": ["symbol", "side"]},
             create_order_intent, risk="write_order", confirm_required=True,
             domain="trading"),
        Tool("adjust_strategy",
             "调整某标的的量化策略参数并热切换(经唯一入口 quant.start)。params 只需给要改的键，"
             "如 {rsi_buy:35, stop_loss:3}。会与现有参数合并。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "params": {"type": "object",
                                        "description": "要修改的策略参数键值"}},
              "required": ["symbol", "params"]},
             adjust_strategy, risk="write_strategy", confirm_required=True,
             domain="quant"),
    ]
    for t in tools:
        if t.name not in REGISTRY:
            register(t)


_register()
