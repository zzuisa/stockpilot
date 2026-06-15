"""T212 行情与交易 API (manage.html 专用)"""
import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import desc, select

import settings
from db import get_db, get_session
from models import News, Price, T212CommunityPost, WatchlistItem, T212WatchlistItem

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/t212", tags=["t212_market"])

# ── 工具 ─────────────────────────────────────────────────────────────────────

def _client():
    if not settings.t212_enabled:
        raise HTTPException(503, "T212 未配置")
    from t212.client import T212
    return T212()


# 全量标的缓存 (TTL 1h)
_inst_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 3600


def _get_instruments() -> list:
    now = time.time()
    if not _inst_cache["data"] or now - _inst_cache["ts"] > _CACHE_TTL:
        log.info("刷新 T212 instruments 缓存")
        _inst_cache["data"] = _client().instruments()
        _inst_cache["ts"] = now
    return _inst_cache["data"]


def _normalize_position(p: dict) -> dict:
    """
    T212 API v0 positions 响应格式:
    {
      "instrument": {"ticker": "NVDII_EQ", "name": "...", "isin": "...", "currency": "USD"},
      "quantity": 1.0,
      "quantityAvailableForTrading": 1.0,
      "currentPrice": 6.31,
      "averagePricePaid": 6.34,
      "walletImpact": {"currency": "EUR", "totalCost": 5.49, "currentValue": 5.46,
                       "unrealizedProfitLoss": -0.03, "fxImpact": -0.01}
    }
    """
    inst = p.get("instrument", {})
    wi = p.get("walletImpact", {})
    return {
        "ticker": inst.get("ticker", ""),
        "name": inst.get("name", ""),
        "isin": inst.get("isin", ""),
        "currency": inst.get("currency", ""),
        "quantity": p.get("quantity", 0),
        "quantityAvailableForTrading": p.get("quantityAvailableForTrading", 0),
        "averagePrice": p.get("averagePricePaid"),
        "currentPrice": p.get("currentPrice"),
        "ppl": wi.get("unrealizedProfitLoss"),
        "totalCost": wi.get("totalCost"),
        "currentValue": wi.get("currentValue"),
        "pnlCurrency": wi.get("currency", "EUR"),
    }


# ── 持仓 ──────────────────────────────────────────────────────────────────────

@router.get("/positions")
def get_positions():
    """返回当前持仓列表 (ticker -> position dict)"""
    raw = _client().positions()
    items = raw if isinstance(raw, list) else raw.get("items", [])
    normalized = [_normalize_position(p) for p in items]
    return {p["ticker"]: p for p in normalized if p["ticker"]}


# ── 搜索 ──────────────────────────────────────────────────────────────────────

@router.get("/search")
def search_instruments(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(30, le=100),
):
    """在 T212 全量标的中按 ticker/名称搜索"""
    instruments = _get_instruments()
    q_lower = q.strip().lower()
    results = []
    for inst in instruments:
        ticker = inst.get("ticker", "")
        name = inst.get("name", "")
        shortName = inst.get("shortName", "")
        if (q_lower in ticker.lower()
                or q_lower in name.lower()
                or q_lower in shortName.lower()):
            results.append({
                "ticker": ticker,
                "name": name,
                "shortName": shortName,
                "type": inst.get("type", ""),
                "currencyCode": inst.get("currencyCode", ""),
                "isin": inst.get("isin", ""),
            })
            if len(results) >= limit:
                break
    return results


# ── 标的动态 (新闻 + 社区) ────────────────────────────────────────────────────

@router.get("/instruments/{ticker}/activity")
def instrument_activity(ticker: str, days: int = Query(7, ge=1, le=30), db=Depends(get_db)):
    """获取标的最近新闻 + T212 社区帖子"""
    # NVDII_EQ → NVDII;  NVDA_US_EQ → NVDA
    raw_sym = ticker.split("_")[0].upper()
    # 尝试从 instruments 缓存匹配标准 symbol（如 NVDA）
    symbol = raw_sym
    since = datetime.now(timezone.utc) - timedelta(days=days)

    news_rows = db.execute(
        select(News)
        .where(News.symbol == symbol, News.published >= since)
        .order_by(desc(News.published))
        .limit(15)
    ).scalars().all()

    try:
        post_rows = db.execute(
            select(T212CommunityPost)
            .where(T212CommunityPost.symbol == symbol,
                   T212CommunityPost.published >= since)
            .order_by(desc(T212CommunityPost.published))
            .limit(15)
        ).scalars().all()
    except Exception:
        post_rows = []

    tracking = db.execute(
        select(WatchlistItem.group_id)
        .where(WatchlistItem.symbol == symbol, WatchlistItem.active == True)
    ).scalars().all()

    return {
        "symbol": symbol,
        "ticker": ticker,
        "tracking_groups": list(tracking),
        "news": [
            {
                "title": n.title,
                "sentiment": n.sentiment,
                "published": n.published,
                "source": n.source,
                "url": n.url,
            }
            for n in news_rows
        ],
        "community": [
            {
                "content": p.content,
                "author": p.author,
                "published": p.published,
                "likes": p.likes,
                "sentiment": p.sentiment,
            }
            for p in post_rows
        ],
    }


# ── 自定义 Watchlist CRUD ────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist(db=Depends(get_db)):
    """获取用户自定义 Watchlist"""
    items = db.execute(select(T212WatchlistItem)).scalars().all()
    return [{"ticker": i.ticker, "name": i.name, "added_at": i.added_at} for i in items]


@router.post("/watchlist")
def add_to_watchlist(body: dict, db=Depends(get_db)):
    """添加 ticker 到 Watchlist. body: {ticker, name?}"""
    ticker = (body.get("ticker") or "").strip()
    if not ticker:
        raise HTTPException(400, "ticker 必填")
    existing = db.get(T212WatchlistItem, ticker)
    if existing:
        return {"ticker": ticker, "msg": "already exists"}
    item = T212WatchlistItem(ticker=ticker, name=body.get("name", ""))
    db.add(item)
    db.commit()
    return {"ticker": ticker, "msg": "added"}


@router.delete("/watchlist/{ticker}")
def remove_from_watchlist(ticker: str, db=Depends(get_db)):
    """从 Watchlist 移除"""
    item = db.get(T212WatchlistItem, ticker)
    if not item:
        raise HTTPException(404, "不在 Watchlist 中")
    db.delete(item)
    db.commit()
    return {"ticker": ticker, "msg": "removed"}


# ── Watchlist 报价（持仓实时 + 非持仓最新收盘） ──────────────────────────────

@router.get("/watchlist/quotes")
def get_watchlist_quotes(db=Depends(get_db)):
    """返回自选列表所有标的的价格与盈亏数据。
    - 持仓标的：从 T212 positions API 取实时价格和盈亏
    - 非持仓标的：从 DB prices 表取最新日线收盘价
    """
    # 1. 自选列表
    wl_items = db.execute(select(T212WatchlistItem)).scalars().all()
    if not wl_items:
        return []

    # 2. T212 持仓（实时）
    positions: dict[str, dict] = {}
    try:
        raw = _client().positions()
        items = raw if isinstance(raw, list) else raw.get("items", [])
        for p in items:
            n = _normalize_position(p)
            if n["ticker"]:
                positions[n["ticker"]] = n
    except Exception as e:
        log.warning("quotes: positions fetch failed: %s", e)

    # 3. 非持仓标的 → DB 最新收盘
    held_tickers = set(positions)
    non_held = [w for w in wl_items if w.ticker not in held_tickers]
    db_prices: dict[str, dict] = {}
    for w in non_held:
        sym = w.ticker.split("_")[0].upper()
        row = db.execute(
            select(Price).where(Price.symbol == sym, Price.interval == "1d")
            .order_by(Price.ts.desc()).limit(1)
        ).scalar_one_or_none()
        if row:
            db_prices[w.ticker] = {
                "close": row.close,
                "ts": row.ts.isoformat() if row.ts else None,
            }

    # 4. 组装结果
    out = []
    for w in wl_items:
        pos = positions.get(w.ticker)
        if pos:
            ppl_pct = None
            if pos.get("totalCost") and pos["totalCost"] != 0:
                ppl_pct = round(
                    (pos.get("ppl") or 0) / pos["totalCost"] * 100, 2)
            out.append({
                "ticker": w.ticker,
                "name": w.name or w.ticker,
                "current_price": pos.get("currentPrice"),
                "avg_price": pos.get("averagePrice"),
                "currency": pos.get("currency", ""),
                "quantity": pos.get("quantity", 0),
                "ppl": pos.get("ppl"),
                "ppl_pct": ppl_pct,
                "total_cost": pos.get("totalCost"),
                "current_value": pos.get("currentValue"),
                "pnl_currency": pos.get("pnlCurrency", "EUR"),
                "source": "position",
            })
        else:
            db_p = db_prices.get(w.ticker, {})
            out.append({
                "ticker": w.ticker,
                "name": w.name or w.ticker,
                "current_price": db_p.get("close"),
                "price_date": db_p.get("ts"),
                "avg_price": None,
                "currency": "",
                "quantity": 0,
                "ppl": None,
                "ppl_pct": None,
                "source": "db_close",
            })
    return out


# ── 实时现价（前端按金额→股数换算用，与下单换算同源） ──────────────────────────

@router.get("/quote/{ticker}")
def get_ticker_quote(ticker: str):
    """返回标的实时现价。每次实时拉取（Finnhub→T212持仓→yfinance），不缓存。"""
    price = _client().live_price(ticker.strip())
    if price <= 0:
        raise HTTPException(502, f"无法获取 {ticker} 实时现价")
    return {"ticker": ticker, "price": price}


# ── 下单 ──────────────────────────────────────────────────────────────────────

@router.post("/orders")
def place_order(body: dict):
    """
    市价单. body:
      {ticker, side: 'buy'|'sell', quantity?: float, value?: float}
    buy  + quantity → 按股数买入
    buy  + value    → 后端实时取现价换算 quantity 后买入(T212 API 仅接受 quantity)
    sell + quantity → 按股数卖出
    金额仅用于换算；最终一律以 quantity 提交。现价始终由后端实时拉取。
    """
    ticker = (body.get("ticker") or "").strip()
    side = (body.get("side") or "buy").lower()
    quantity = body.get("quantity")
    value = body.get("value")

    if not ticker:
        raise HTTPException(400, "ticker 必填")
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side 必须为 buy 或 sell")
    if quantity is None and value is None:
        raise HTTPException(400, "quantity 或 value 必填其一")

    client = _client()
    env = settings.T212_ENV or "demo"

    try:
        if side == "sell":
            qty = float(quantity)
            if qty <= 0:
                raise HTTPException(400, "卖出 quantity 须为正数")
            result = client.market_order(ticker, -qty)
        elif value is not None:
            # 后端实时取现价换算股数，仅用 quantity 下单
            result = client.market_order_value(ticker, float(value))
        else:
            result = client.market_order(ticker, float(quantity))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.warning("T212 order failed: %s", e)
        raise HTTPException(502, f"T212 下单失败: {e}")

    return {"env": env, "result": result}


# ── 挂单查询 ─────────────────────────────────────────────────────────────────

@router.get("/orders/open")
def get_open_orders():
    """返回当前全部未成交挂单（限价/止损单等）"""
    client = _client()
    try:
        raw = client.open_orders()
        items = raw if isinstance(raw, list) else raw.get("items", [])
    except Exception as e:
        log.warning("获取挂单失败: %s", e)
        raise HTTPException(502, f"T212 获取挂单失败: {e}")
    return {"items": items, "count": len(items)}


# ── 限价单 ────────────────────────────────────────────────────────────────────

@router.post("/orders/limit")
def place_limit_order(body: dict):
    """
    限价单. body:
      {ticker, side: 'buy'|'sell', quantity: float, limitPrice: float,
       timeValidity: 'DAY'|'GOOD_TILL_CANCEL'}
    """
    ticker       = (body.get("ticker") or "").strip()
    side         = (body.get("side") or "buy").lower()
    quantity     = body.get("quantity")
    limit_price  = body.get("limitPrice")
    time_val     = body.get("timeValidity", "DAY").upper()

    if not ticker:
        raise HTTPException(400, "ticker 必填")
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side 必须为 buy 或 sell")
    if quantity is None or limit_price is None:
        raise HTTPException(400, "quantity 和 limitPrice 必填")
    if time_val not in ("DAY", "GOOD_TILL_CANCEL"):
        raise HTTPException(400, "timeValidity 须为 DAY 或 GOOD_TILL_CANCEL")

    qty = float(quantity)
    if qty <= 0:
        raise HTTPException(400, "quantity 须为正数（方向由 side 决定）")
    if side == "sell":
        qty = -qty

    client = _client()
    try:
        result = client.limit_order(ticker, qty, float(limit_price), time_val)
    except Exception as e:
        log.warning("T212 limit order failed: %s", e)
        raise HTTPException(502, f"T212 限价单失败: {e}")

    return {"env": settings.T212_ENV or "demo", "result": result}


# ── 止损单 ────────────────────────────────────────────────────────────────────

@router.post("/orders/stop")
def place_stop_order(body: dict):
    """
    止损/止盈触发单. body:
      {ticker, side: 'buy'|'sell', quantity: float, stopPrice: float,
       timeValidity: 'DAY'|'GOOD_TILL_CANCEL'}
    """
    ticker      = (body.get("ticker") or "").strip()
    side        = (body.get("side") or "buy").lower()
    quantity    = body.get("quantity")
    stop_price  = body.get("stopPrice")
    time_val    = body.get("timeValidity", "DAY").upper()

    if not ticker:
        raise HTTPException(400, "ticker 必填")
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side 必须为 buy 或 sell")
    if quantity is None or stop_price is None:
        raise HTTPException(400, "quantity 和 stopPrice 必填")
    if time_val not in ("DAY", "GOOD_TILL_CANCEL"):
        raise HTTPException(400, "timeValidity 须为 DAY 或 GOOD_TILL_CANCEL")

    qty = float(quantity)
    if qty <= 0:
        raise HTTPException(400, "quantity 须为正数")
    if side == "sell":
        qty = -qty

    client = _client()
    try:
        result = client.stop_order(ticker, qty, float(stop_price), time_val)
    except Exception as e:
        log.warning("T212 stop order failed: %s", e)
        raise HTTPException(502, f"T212 止损单失败: {e}")

    return {"env": settings.T212_ENV or "demo", "result": result}


# ── 取消挂单 ──────────────────────────────────────────────────────────────────

@router.delete("/orders/{order_id}")
def cancel_order(order_id: int):
    """取消指定挂单"""
    client = _client()
    try:
        client.cancel_order(order_id)
    except Exception as e:
        log.warning("T212 cancel order %s failed: %s", order_id, e)
        raise HTTPException(502, f"T212 取消挂单失败: {e}")
    return {"order_id": order_id, "msg": "已取消"}


# ── 波段交易 ──────────────────────────────────────────────────────────────────

@router.post("/band")
def place_band(body: dict):
    """
    波段交易：同时挂 sell limit + buy limit，使用 1 req/2s 最高频率。
    body:
      {ticker, buyLimitPrice: float, sellLimitPrice: float,
       buyQty: float, sellQty: float,
       timeValidity: 'DAY'|'GOOD_TILL_CANCEL'}
    卖出价应高于当前价（止盈），买入价应低于当前价（逢低加仓）。
    """
    ticker      = (body.get("ticker") or "").strip()
    buy_price   = body.get("buyLimitPrice")
    sell_price  = body.get("sellLimitPrice")
    buy_qty     = body.get("buyQty")
    sell_qty    = body.get("sellQty")
    time_val    = body.get("timeValidity", "GOOD_TILL_CANCEL").upper()

    if not ticker:
        raise HTTPException(400, "ticker 必填")
    if buy_price is None and sell_price is None:
        raise HTTPException(400, "buyLimitPrice 或 sellLimitPrice 至少填一个")
    if time_val not in ("DAY", "GOOD_TILL_CANCEL"):
        raise HTTPException(400, "timeValidity 须为 DAY 或 GOOD_TILL_CANCEL")

    client = _client()
    results = {}

    # 先下卖出限价单（止盈），再等 2s 下买入限价单（抄底）
    if sell_price is not None and sell_qty:
        sq = float(sell_qty)
        if sq <= 0:
            raise HTTPException(400, "sellQty 须为正数")
        try:
            results["sell_limit"] = client.limit_order(ticker, -sq, float(sell_price), time_val)
        except Exception as e:
            log.warning("band sell limit failed: %s", e)
            raise HTTPException(502, f"卖出限价单失败: {e}")

    if buy_price is not None and buy_qty:
        bq = float(buy_qty)
        if bq <= 0:
            raise HTTPException(400, "buyQty 须为正数")
        try:
            results["buy_limit"] = client.limit_order(ticker, bq, float(buy_price), time_val)
        except Exception as e:
            log.warning("band buy limit failed: %s", e)
            # 若卖单已成功，把已下部分一起返回，让前端知道部分成功
            results["buy_limit_error"] = str(e)

    return {"env": settings.T212_ENV or "demo", "results": results}


# ── 自选标的即时刷新 ──────────────────────────────────────────────────────────

@router.post("/watchlist/symbols/{symbol}/refresh")
async def refresh_symbol_data(symbol: str, background_tasks: BackgroundTasks,
                              db=Depends(get_db)):
    """新加入自选的标的：立即拉取价格 / 新闻 / 指标，不等日定时任务。"""
    sym = symbol.upper()
    item = db.query(WatchlistItem).filter(
        WatchlistItem.symbol == sym, WatchlistItem.active).first()
    if not item:
        raise HTTPException(404, f"{sym} 不在自选列表中")
    background_tasks.add_task(_refresh_symbol_bg, sym)
    return {"ok": True, "symbol": sym}


async def _refresh_symbol_bg(symbol: str):
    def work():
        from collectors import news, prices
        from analysis import indicators
        with get_session() as s:
            item = s.query(WatchlistItem).filter(WatchlistItem.symbol == symbol).first()
            yf_sym = ((item.symbol_config or {}).get("yf_symbol") if item else None) or symbol
            yf_map = {symbol: yf_sym}
            prices.fetch_daily([symbol], s, period="60d", yf_map=yf_map)
            news.fetch_finnhub([symbol], s, days=7)
            indicators.compute_all(s)
    try:
        await asyncio.to_thread(work)
        log.info("refresh_symbol %s done", symbol)
    except Exception as e:
        log.warning("refresh_symbol %s failed: %s", symbol, e)
