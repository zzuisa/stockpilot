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
    buy_mode: str = "turning"     # "turning"=日内拐点低买(默认); "ind"=RSI+MACD; "market"=空仓即买
    rsi_buy: float = 45
    rsi_sell: float = 55
    stop_loss: float = 2.0        # 硬止损 %
    safe_mode: bool = False       # 只赚不亏：只在止盈卖出，不因浮亏止损(仅灾难止损兜底)
    disaster_stop_pct: float = 25.0  # safe_mode 下的灾难止损兜底 %
    profit_pct: float = 0.5       # 盈利目标 %(高于均价此比例时卖出)
    budget_ratio: float = 50.0    # 每次买入占可用现金比例 %
    sell_ratio: float = 100.0     # 每次卖出占持仓比例 %
    budget_eur: float = 1000.0    # 单笔买入上限(按 currency 计;0=不限)
    currency: str = "USD"         # 下单金额币种(默认美元)
    interval: int = 5
    max_trades_day: int = 50
    slippage_pct: float = 0.5     # 24/5 可成交限价滑点 %(买高卖低跨价立即成交)
    # 拐点低买高卖(buy_mode=turning)算法参数
    turn_tf: str = "intraday"     # intraday=日内采样高频拐点(默认); daily=日线级 PIP
    turn_window: int = 180        # 日内：采样根数(配合 60s 采样 ≈ 3 小时)
    turn_sample_sec: int = 60     # 日内：拐点序列采样间隔(秒)
    turn_beta: float = 4          # 日内：swing 半径(采样根≈分钟)；daily=PIP 最小间隔(天)
    turn_rebound_pct: float = 0.2  # 日内：自谷反弹/自峰回落 ≥ 此 % 才确认拐点
    turn_recent: int = 3          # 日内：拐点确认后多少根采样内仍可买入
    turn_recent_days: int = 8     # daily：谷确认后 N 个交易日内仍可买入
    buy_discount_pct: float = 0.0  # 低买折扣 %(buy-limit 低于现价)
    sell_at_peak: bool = True     # 高卖目标取算法识别的峰价(高于止盈目标时)
    explain_llm: bool = True      # 每次触发动作用 LLM 生成决策解释(best-effort)


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
    if body.buy_mode not in ("ind", "market", "turning"):
        raise HTTPException(422, "buy_mode 须为 ind / market / turning")
    if not (0.1 <= body.stop_loss <= 20):
        raise HTTPException(422, "stop_loss 取值 0.1–20 (%)")
    if body.profit_pct <= 0:
        raise HTTPException(422, "profit_pct 必须 > 0")
    if not (1 <= body.budget_ratio <= 100):
        raise HTTPException(422, "budget_ratio 取值 1–100 (%)")
    if not (1 <= body.sell_ratio <= 100):
        raise HTTPException(422, "sell_ratio 取值 1–100 (%)")
    from t212.account_cache import get_active as _get_active
    acct = _get_active()
    if not acct:
        raise HTTPException(503, "未配置 T212 账户，请先在「账户」页面添加 API Key")
    params = body.model_dump(exclude={"t212_ticker"})
    runner = quant.start(symbol, body.t212_ticker, params,
                         account_id=acct["id"], api_key=acct["api_key"],
                         api_secret=acct.get("api_secret"), env=acct["env"])
    log.info("quant start %s (account=%s env=%s)", symbol, acct["id"], acct["env"])
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
