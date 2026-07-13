"""托管自主循环（Phase 6）：分析 → 反思 → 决策 → 执行。

每个开启托管的标的跑一轮 supervisor（allow_write=True，可 create_order_intent / adjust_strategy），
随后对本轮新建的 pending 订单意向按**风险预算**结算：
- 单笔 ≤ auto_execute_max_eur 且未超每日自动成交笔数 → 直接执行（executor，含风控二次校验）；
- 超预算 → 留 pending 并**升级为人工确认**（Telegram 卡片），网页也可确认。

kill-switch / 托管关 → 整轮跳过，不产生任何自动执行。反思记忆走 supervisor 内置的 thesis distill。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from analysis import appsettings

log = logging.getLogger(__name__)

_AUTONOMY_PROMPT = (
    "现在对该标的做一次自主复盘：结合最新市场数据、情绪、既有研究档案与到期验证点，"
    "判断(1)当前量化策略参数是否需要调整——如需，调用 adjust_strategy 落地；"
    "(2)是否存在明确的建仓/减仓时机——如有，调用 create_order_intent 建待确认单(只建不下)。"
    "没有把握就不动手，明确说明维持现状的理由。最后给出下一验证点。"
)


async def run_autonomy_cycle(symbol: str) -> dict:
    """跑一个标的的托管循环。返回 {symbol, ran, executed, escalated, skipped_reason?}。"""
    sym = symbol.split("_")[0].upper()
    if not appsettings.autonomy_enabled(sym):
        return {"symbol": sym, "ran": False, "skipped_reason": "autonomy off / kill-switch"}

    from agents.supervisor import run_supervisor
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    try:
        await run_supervisor(sym, _AUTONOMY_PROMPT, mode="autonomy", allow_write=True)
    except Exception as e:                       # noqa: BLE001
        log.warning("autonomy supervisor %s 失败: %s", sym, e)
        return {"symbol": sym, "ran": False, "skipped_reason": f"supervisor error: {e}"}

    settled = await asyncio.to_thread(_settle_intents, sym, since)
    return {"symbol": sym, "ran": True, **settled}


def _settle_intents(symbol: str, since: datetime) -> dict:
    """对本轮新建的 pending 意向按预算结算：执行 or 升级人工确认。"""
    from db import get_session
    from models import OrderIntent
    from sqlalchemy import select
    from trading import executor

    cap = appsettings.auto_execute_cap(symbol)
    cfg = appsettings.get_for(symbol)
    daily_cap = int((cfg.get("risk_budget") or {}).get("daily_auto_trades", 0) or 0)
    executed, escalated = [], []

    with get_session() as db:
        rows = db.execute(
            select(OrderIntent).where(
                OrderIntent.symbol == symbol,
                OrderIntent.status == "pending",
                OrderIntent.created_at >= since)).scalars().all()
        pending = [(r.id, float(r.order_value_eur or 0)) for r in rows]
        today_auto = _today_auto_count(db, symbol)

    for iid, val in pending:
        if appsettings.kill_switch():
            escalated.append(iid)
            continue
        if cap > 0 and val <= cap and today_auto < daily_cap:
            res = executor.execute_intent(iid, "autonomy")
            if res.get("ok"):
                executed.append(iid)
                today_auto += 1
            else:
                escalated.append(iid)          # 风控/执行失败 → 留待人工看
        else:
            _escalate(symbol, iid, val, cap)
            escalated.append(iid)
    return {"executed": executed, "escalated": escalated}


def _today_auto_count(db, symbol: str) -> int:
    from models import OrderIntent
    from sqlalchemy import select, func
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    n = db.execute(
        select(func.count()).select_from(OrderIntent).where(
            OrderIntent.symbol == symbol,
            OrderIntent.confirmed_by == "autonomy",
            OrderIntent.status == "executed",
            OrderIntent.updated_at >= start)).scalar()
    return int(n or 0)


def _escalate(symbol: str, intent_id: str, val: float, cap: float) -> None:
    """超预算 → 推送升级为人工确认（复用现有 Telegram 信号卡片）。"""
    try:
        import settings
        from notify.telegram import TelegramSender
        text = (f"🤖 托管建议（超自动执行预算 €{cap}）：{symbol} 单笔 ≈€{val:.0f} 待你确认。")
        chat = settings.TELEGRAM_CHAT_ID
        if chat:
            asyncio.get_event_loop().create_task(
                TelegramSender().send_signal_card(chat, text, intent_id))
    except Exception as e:                       # noqa: BLE001
        log.warning("escalate %s 推送失败: %s", intent_id[:8], e)


async def run_all() -> dict:
    """扫描所有开启托管的标的，逐个跑一轮。供 job_autonomy 调用。"""
    if appsettings.kill_switch() or not appsettings.get_global().get("autonomy_enabled"):
        return {"ran": 0, "reason": "global autonomy off / kill-switch"}
    symbols = _autonomy_symbols()
    results = []
    for sym in symbols:
        results.append(await run_autonomy_cycle(sym))
    ran = sum(1 for r in results if r.get("ran"))
    log.info("autonomy 循环完成：%d/%d 标的运行", ran, len(symbols))
    return {"ran": ran, "total": len(symbols), "results": results}


def _autonomy_symbols() -> list[str]:
    """哪些标的参与托管：有运行中量化策略的标的（可按 sym 覆盖开关进一步过滤）。"""
    try:
        import quant
        syms = [r.symbol for r in quant.list_runners()]
    except Exception:
        syms = []
    return [s for s in syms if appsettings.autonomy_enabled(s)]
