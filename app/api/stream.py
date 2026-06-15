"""实时成交流 SSE — Finnhub WebSocket → 服务端事件推送到浏览器"""
import asyncio
import json
import logging
from collections import deque

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

import settings
from db import get_db
from models import WatchlistItem

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/stream", tags=["stream"])


class _Feed:
    """单 symbol 的 Finnhub WS 订阅 + 多客户端广播"""

    MAX_BUF = 300

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.buf: deque[dict] = deque(maxlen=self.MAX_BUF)
        self._queues: list[asyncio.Queue] = []
        self._task: asyncio.Task | None = None
        self._last_price: float | None = None

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        self._ensure_running()
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def _broadcast(self, msg: dict):
        for q in list(self._queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def _run(self):
        import websockets  # lazy import — only needed when streaming

        url = f"wss://ws.finnhub.io?token={settings.FINNHUB_TOKEN}"
        backoff = 2
        while self._queues:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    await ws.send(json.dumps({"type": "subscribe",
                                              "symbol": self.symbol}))
                    log.info("finnhub ws subscribed: %s", self.symbol)
                    backoff = 2
                    async for raw in ws:
                        if not self._queues:
                            break
                        data = json.loads(raw)
                        if data.get("type") != "trade":
                            continue
                        for t in data.get("data", []):
                            price = float(t.get("p", 0))
                            volume = float(t.get("v", 0))
                            ts_ms = int(t.get("t", 0))
                            direction = 0
                            if self._last_price is not None:
                                if price > self._last_price:
                                    direction = 1
                                elif price < self._last_price:
                                    direction = -1
                            self._last_price = price
                            msg = {"p": round(price, 4),
                                   "v": round(volume, 2),
                                   "t": ts_ms,
                                   "d": direction}
                            self.buf.append(msg)
                            self._broadcast(msg)
            except Exception as e:
                if self._queues:
                    log.warning("finnhub ws %s error: %s, retry in %ds",
                                self.symbol, e, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                else:
                    break
        log.info("finnhub ws stopped: %s (no clients)", self.symbol)


_feeds: dict[str, _Feed] = {}


def _get_feed(symbol: str) -> _Feed:
    sym = symbol.upper()
    if sym not in _feeds:
        _feeds[sym] = _Feed(sym)
    return _feeds[sym]


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/trades/{symbol}")
async def stream_trades(symbol: str, request: Request):
    """SSE 实时成交流：先推历史缓冲（已连接时立即可见），再推新成交。"""
    if not settings.finnhub_enabled:
        async def _err():
            yield 'data: {"error":"FINNHUB_TOKEN 未配置"}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream")

    feed = _get_feed(symbol.upper())
    history = list(feed.buf)       # snapshot before subscribe
    q = await feed.subscribe()

    async def generate():
        try:
            for msg in history:
                yield f"data: {json.dumps(msg)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            feed.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/quote/{symbol}")
async def get_quote(symbol: str, db=Depends(get_db)):
    """当前报价：三级降级 Finnhub(美股) → T212持仓(实时) → yfinance(~15min延迟)"""
    sym = symbol.upper()

    # 1. Finnhub（美股实时）
    if settings.finnhub_enabled:
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": sym, "token": settings.FINNHUB_TOKEN},
                )
                r.raise_for_status()
                d = r.json()
                if d.get("c"):
                    return {
                        "bid":        d.get("b") or d.get("c"),
                        "ask":        d.get("a") or d.get("c"),
                        "last":       d["c"],
                        "open":       d.get("o"),
                        "high":       d.get("h"),
                        "low":        d.get("l"),
                        "prev_close": d.get("pc"),
                        "change_pct": round((d["c"] - d["pc"]) / d["pc"] * 100, 2)
                                      if d.get("pc") else None,
                        "source":     "finnhub",
                    }
        except Exception:
            pass

    # 2. T212 持仓（非美股持仓时实时）
    if settings.t212_enabled:
        try:
            def _t212():
                from t212.client import T212
                raw = T212().positions()
                items = raw if isinstance(raw, list) else raw.get("items", [])
                for p in items:
                    ticker = p.get("instrument", {}).get("ticker", "")
                    if ticker.split("_")[0].upper() == sym:
                        return p.get("currentPrice"), p.get("averagePricePaid")
                return None, None
            price, avg = await asyncio.to_thread(_t212)
            if price:
                chg = round((price - avg) / avg * 100, 2) if avg else None
                return {"bid": price, "ask": price, "last": price,
                        "prev_close": avg, "change_pct": chg, "source": "t212_position"}
        except Exception:
            pass

    # 3. yfinance（非美股，~15min 延迟；从 DB 取 yf_symbol 覆盖）
    try:
        row = db.query(WatchlistItem).filter(
            WatchlistItem.symbol == sym, WatchlistItem.active).first()
        yf_sym = ((row.symbol_config or {}).get("yf_symbol") if row else None) or sym

        def _yf():
            import yfinance as yf
            intra = yf.Ticker(yf_sym).history(period="1d", interval="1m", auto_adjust=True)
            closes = intra["Close"].dropna()
            if closes.empty:
                return None, None
            p = float(closes.iloc[-1])
            daily = yf.Ticker(yf_sym).history(period="5d", interval="1d", auto_adjust=True)
            dc = daily["Close"].dropna()
            pc = float(dc.iloc[-2]) if len(dc) >= 2 else None
            return p, pc

        price, prev_close = await asyncio.to_thread(_yf)
        if price:
            chg = round((price - prev_close) / prev_close * 100, 2) if prev_close else None
            return {"bid": price, "ask": price, "last": price,
                    "prev_close": prev_close, "change_pct": chg, "source": "yfinance"}
    except Exception as e:
        log.warning("quote %s failed: %s", symbol, e)
        return {}
