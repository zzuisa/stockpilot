"""量化交易 API — 启停策略 / 状态 / 成交记录"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import quant
import settings
from db import get_db
from models import QuantStrategy, QuantTrade

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quant", tags=["quant"])


class StrategyStart(BaseModel):
    t212_ticker: str
    buy_mode: str = "ind"         # "ind"=RSI+MACD; "market"=空仓即买
    rsi_buy: float = 45
    rsi_sell: float = 55
    stop_loss: float = 1.0        # 硬止损 %
    profit_pct: float = 2.0       # 盈利目标 %(高于均价此比例时卖出)
    budget_ratio: float = 50.0    # 每次买入占可用现金比例 %
    sell_ratio: float = 100.0     # 每次卖出占持仓比例 %
    budget_eur: float = 0.0       # 单笔买入上限 €(0=不限)
    interval: int = 5
    max_trades_day: int = 10


@router.get("/strategies")
async def list_strategies(db=Depends(get_db)):
    """全部策略:数据库配置 + 运行时状态"""
    rows = db.execute(select(QuantStrategy)).scalars().all()
    out = []
    seen = set()
    for r in rows:
        runner = quant.get_runner(r.symbol)
        item = {"symbol": r.symbol, "t212_ticker": r.t212_ticker,
                "params": r.params, "active": r.active,
                "env": settings.T212_ENV}
        if runner:
            item.update(runner.status())
        else:
            item["running"] = False
        out.append(item)
        seen.add(r.symbol)
    # 内存中有但库里没有的(理论上不会发生)
    for runner in quant.list_runners():
        if runner.symbol not in seen:
            out.append(runner.status())
    return out


@router.get("/strategies/{symbol}")
async def get_strategy(symbol: str, db=Depends(get_db)):
    sym = symbol.upper()
    runner = quant.get_runner(sym)
    if runner:
        return runner.status()
    row = db.get(QuantStrategy, sym)
    if not row:
        raise HTTPException(404, "策略不存在")
    return {"symbol": row.symbol, "t212_ticker": row.t212_ticker,
            "params": row.params, "active": row.active,
            "running": False, "env": settings.T212_ENV}


@router.post("/strategies/{symbol}/start")
async def start_strategy(symbol: str, body: StrategyStart):
    if not settings.t212_enabled:
        raise HTTPException(503, "T212 未配置")
    if body.buy_mode not in ("ind", "market"):
        raise HTTPException(422, "buy_mode 须为 ind 或 market")
    if not (0.1 <= body.stop_loss <= 20):
        raise HTTPException(422, "stop_loss 取值 0.1–20 (%)")
    if body.profit_pct <= 0:
        raise HTTPException(422, "profit_pct 必须 > 0")
    if not (1 <= body.budget_ratio <= 100):
        raise HTTPException(422, "budget_ratio 取值 1–100 (%)")
    if not (1 <= body.sell_ratio <= 100):
        raise HTTPException(422, "sell_ratio 取值 1–100 (%)")
    params = body.model_dump(exclude={"t212_ticker"})
    runner = quant.start(symbol, body.t212_ticker, params)
    log.info("quant start %s by api (env=%s)", symbol, settings.T212_ENV)
    return runner.status()


@router.post("/strategies/{symbol}/stop")
async def stop_strategy(symbol: str):
    stopped = quant.stop(symbol)
    return {"ok": True, "stopped": stopped}


@router.get("/trades")
async def list_trades(symbol: str | None = None, limit: int = 100,
                      db=Depends(get_db)):
    q = select(QuantTrade).order_by(QuantTrade.ts.desc()).limit(min(limit, 500))
    if symbol:
        q = q.where(QuantTrade.symbol == symbol.upper())
    return [{"ts": t.ts, "symbol": t.symbol, "side": t.side,
             "reason": t.reason, "quantity": t.quantity, "price": t.price,
             "pnl": t.pnl, "order_id": t.order_id}
            for t in db.execute(q).scalars()]
