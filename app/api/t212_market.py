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
from trades import record_trade

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/t212", tags=["t212_market"])

# ── 工具 ─────────────────────────────────────────────────────────────────────

def _client():
    """返回激活账户的 T212 客户端"""
    from t212.account_cache import get_client
    return get_client()


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


# ── 实时现金/账户 ─────────────────────────────────────────────────────────────

@router.get("/cash")
def get_cash():
    """实时账户现金(账户主币种)。total=总资产, free=可用, ppl=浮动盈亏。"""
    try:
        c = _client().cash() or {}
    except Exception as e:
        log.warning("cash 获取失败: %s", e)
        raise HTTPException(502, f"T212 获取现金失败: {e}")
    return {
        "free": c.get("free"),
        "total": c.get("total"),
        "invested": c.get("invested"),
        "ppl": c.get("ppl"),
        "result": c.get("result"),
        "blocked": c.get("blocked"),
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


# ── 历史日线（近 N 天，供行情详情 K 线；数据由夜间采集入库） ─────────────────────

@router.get("/prices/{symbol}")
def price_history(symbol: str, days: int = Query(30, ge=5, le=365), db=Depends(get_db)):
    """返回标的近 N 天日线 OHLCV（用于行情详情 K 线图 + 框选归因）。"""
    sym = symbol.split("_")[0].upper()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(Price.ts, Price.open, Price.high, Price.low, Price.close, Price.volume)
        .where(Price.symbol == sym, Price.interval == "1d", Price.ts >= since)
        .order_by(Price.ts)).all()
    return {"symbol": sym, "candles": [
        {"t": r[0].isoformat(), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
        for r in rows]}


# ── 按需分钟级 K 线（yfinance 实时拉取 + 60s 内存缓存），供详情图缩放到分钟 ─────────
_INTRADAY_ALLOWED = {"1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"}
# yfinance 各周期回溯上限（天）
_INTRADAY_MAXDAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
                     "60m": 730, "1h": 730, "1d": 3650}
_intraday_cache: dict = {}          # key=(sym,interval,days) → {"ts":epoch,"data":dict}
_INTRADAY_TTL = 60.0


def _yf_symbol_for(sym: str) -> str:
    """从 watchlist 取 yf_symbol 覆盖（德股等需交易所后缀），无则用 sym 本身。"""
    try:
        with get_session() as s:
            import config
            for d in config.active_symbols(s):
                if d["symbol"] == sym:
                    return d.get("yf_symbol") or sym
    except Exception:
        pass
    return sym


@router.get("/prices/{symbol}/intraday")
async def price_intraday(
    symbol: str,
    interval: str = Query("5m"),
    days: int = Query(30, ge=1, le=730),
):
    """按需分钟/小时级 K 线（yfinance 实时）。interval ∈ 1m/2m/5m/15m/30m/60m/1h/1d，
    days 按该周期 yfinance 回溯上限自动夹取。服务端 60s 缓存以控频。"""
    sym = symbol.split("_")[0].upper()
    if interval not in _INTRADAY_ALLOWED:
        raise HTTPException(400, f"interval 必须是 {sorted(_INTRADAY_ALLOWED)}")
    days = min(days, _INTRADAY_MAXDAYS.get(interval, 60))
    key = (sym, interval, days)
    now = time.time()
    hit = _intraday_cache.get(key)
    if hit and now - hit["ts"] < _INTRADAY_TTL:
        return hit["data"]

    yf_sym = _yf_symbol_for(sym)

    def _fetch():
        from collectors.prices import _download
        frames = _download([sym], yf_map={sym: yf_sym},
                           period=f"{days}d", interval=interval)
        df = frames.get(sym)
        candles = []
        if df is not None and not df.empty:
            for ts, row in df.iterrows():
                try:
                    o, h, l, c, v = (row["Open"], row["High"], row["Low"],
                                     row["Close"], row.get("Volume"))
                    if c != c:            # NaN 收盘 → 跳过（停牌/空档）
                        continue
                    pyts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                    candles.append({
                        "t": pyts.isoformat(),
                        "o": None if o != o else round(float(o), 4),
                        "h": None if h != h else round(float(h), 4),
                        "l": None if l != l else round(float(l), 4),
                        "c": round(float(c), 4),
                        "v": 0 if v is None or v != v else int(v),
                    })
                except Exception:
                    continue
        return {"symbol": sym, "interval": interval, "candles": candles}

    data = await asyncio.to_thread(_fetch)
    _intraday_cache[key] = {"ts": now, "data": data}
    return data


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
    currency = (body.get("currency") or "USD").upper()

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
            # 后端实时取现价换算股数，仅用 quantity 下单（currency 仅用于前端展示/余额提示）
            result = client.market_order_value(ticker, float(value))
        else:
            result = client.market_order(ticker, float(quantity))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.warning("T212 order failed: %s", e)
        if "insufficient-free-for-stocks-buy" in str(e) or "Insufficient funds" in str(e):
            raise HTTPException(400,
                "资金不足：买入该美股需要美元(USD)余额，而账户当前为欧元。"
                "请先在 T212 App 内把 EUR 兑换为 USD（即可避免每单换汇费），或减小买入金额。")
        raise HTTPException(502, f"T212 下单失败: {e}")

    record_trade(source="manual", side=side, t212_ticker=ticker,
                 order_type="market",
                 quantity=abs(float(result.get("filledQuantity") or result.get("quantity") or 0)) or None,
                 price=result.get("fillResult", {}).get("fillPrice") if isinstance(result.get("fillResult"), dict) else None,
                 value_eur=float(value) if value is not None else None,
                 currency=(currency if value is not None else None),
                 reason="手动市价", status="submitted",
                 order_id=result.get("id"))
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

    record_trade(source="manual", side=side, t212_ticker=ticker,
                 order_type="limit", quantity=abs(qty), price=float(limit_price),
                 reason="手动限价", status="submitted", order_id=result.get("id"))
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

    record_trade(source="manual", side=side, t212_ticker=ticker,
                 order_type="stop", quantity=abs(qty), price=float(stop_price),
                 reason="手动止损", status="submitted", order_id=result.get("id"))
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
            record_trade(source="manual", side="sell", t212_ticker=ticker,
                         order_type="band_sell", quantity=sq, price=float(sell_price),
                         reason="波段止盈", status="submitted",
                         order_id=results["sell_limit"].get("id"))
        except Exception as e:
            log.warning("band sell limit failed: %s", e)
            raise HTTPException(502, f"卖出限价单失败: {e}")

    if buy_price is not None and buy_qty:
        bq = float(buy_qty)
        if bq <= 0:
            raise HTTPException(400, "buyQty 须为正数")
        try:
            results["buy_limit"] = client.limit_order(ticker, bq, float(buy_price), time_val)
            record_trade(source="manual", side="buy", t212_ticker=ticker,
                         order_type="band_buy", quantity=bq, price=float(buy_price),
                         reason="波段抄底", status="submitted",
                         order_id=results["buy_limit"].get("id"))
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
