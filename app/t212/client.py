"""Trading212 API 客户端(说明书 §6)"""
import base64
import time

import httpx

import settings


def _basic_auth_header() -> str:
    """T212 API v0 用 HTTP Basic Auth: base64(api_key:api_secret)。
    等价于 Postman 里 Basic Auth 标签自动做的编码。"""
    raw = f"{settings.T212_API_KEY}:{settings.T212_API_SECRET}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


BASE = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}[settings.T212_ENV or "demo"]


class T212:
    def __init__(self):
        if not settings.t212_enabled:
            raise RuntimeError("T212_API_KEY 未配置")
        self.h = {"Authorization": _basic_auth_header()}
        self._last_call = {}

    def _throttle(self, path, min_interval=2.0):
        now = time.monotonic()
        wait = self._last_call.get(path, 0) + min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_call[path] = time.monotonic()

    def _get(self, path, **params):
        self._throttle(path)
        for attempt in range(4):
            r = httpx.get(f"{BASE}{path}", headers=self.h,
                          params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited: {path}")

    def positions(self):
        return self._get("/equity/positions")

    def cash(self):
        return self._get("/equity/account/cash")

    def open_orders(self):
        self._throttle("/equity/orders", 5.0)  # 1 req/5s
        for attempt in range(4):
            r = httpx.get(f"{BASE}/equity/orders", headers=self.h, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("rate limited: /equity/orders")

    def order_history(self, **kw):
        return self._get("/equity/history/orders", **kw)

    def dividends(self, **kw):
        return self._get("/equity/history/dividends", **kw)

    def watchlists(self) -> list:
        return self._get("/equity/watchlists")

    def instruments(self) -> list:
        """返回全部可交易标的(数量大, 建议调用方缓存)"""
        return self._get("/equity/metadata/instruments")

    def market_order(self, ticker: str, quantity: float):
        """按股数下市价单 (quantity>0 买入, quantity<0 卖出)"""
        self._throttle("/equity/orders/market", 3.0)
        r = httpx.post(f"{BASE}/equity/orders/market", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity}, timeout=30)
        r.raise_for_status()
        return r.json()

    def live_price(self, ticker: str) -> float:
        """实时现价（每次重新拉取，不依赖前端静态值）。
        三级降级: Finnhub(美股实时) → T212 持仓现价 → yfinance(~15min 延迟)。
        返回 0.0 表示获取失败。"""
        sym = ticker.split("_")[0].upper()

        # 1) Finnhub 实时报价（美股）
        if settings.FINNHUB_TOKEN:
            try:
                r = httpx.get("https://finnhub.io/api/v1/quote",
                              params={"symbol": sym, "token": settings.FINNHUB_TOKEN},
                              timeout=6)
                r.raise_for_status()
                c = r.json().get("c")
                if c:
                    return float(c)
            except Exception:
                pass

        # 2) T212 持仓现价（已持有时可用）
        try:
            raw = self.positions()
            items = raw if isinstance(raw, list) else raw.get("items", [])
            for p in items:
                if (p.get("instrument") or {}).get("ticker") == ticker:
                    cp = p.get("currentPrice")
                    if cp:
                        return float(cp)
        except Exception:
            pass

        # 3) yfinance（非美股兜底，有延迟）
        try:
            import yfinance as yf
            h = yf.Ticker(sym).history(period="1d", interval="1m", auto_adjust=True)
            closes = h["Close"].dropna()
            if not closes.empty:
                return float(closes.iloc[-1])
        except Exception:
            pass

        return 0.0

    def market_order_value(self, ticker: str, value: float):
        """按金额(€)买入：每次实时取现价换算成股数，只用 quantity 下单
        （T212 市价单 API 仅接受 quantity）。"""
        price = self.live_price(ticker)
        if price <= 0:
            raise ValueError(f"无法获取 {ticker} 实时现价，无法按金额下单")
        qty = round(value / price, 4)
        if qty <= 0:
            raise ValueError(f"按金额 {value}€ 换算股数为 0（现价 {price}），请增大金额")
        return self.market_order(ticker, qty)

    def limit_order(self, ticker: str, quantity: float, limit_price: float,
                    time_validity: str = "DAY"):
        """限价单 (quantity>0 买入, quantity<0 卖出). 1 req/2s."""
        self._throttle("/equity/orders/limit", 2.0)
        r = httpx.post(f"{BASE}/equity/orders/limit", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity,
                             "limitPrice": limit_price, "timeValidity": time_validity},
                       timeout=30)
        r.raise_for_status()
        return r.json()

    def stop_order(self, ticker: str, quantity: float, stop_price: float,
                   time_validity: str = "DAY"):
        """止损/止盈触发单 (quantity>0 买入触发, quantity<0 卖出触发). 1 req/2s."""
        self._throttle("/equity/orders/stop", 2.0)
        r = httpx.post(f"{BASE}/equity/orders/stop", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity,
                             "stopPrice": stop_price, "timeValidity": time_validity},
                       timeout=30)
        r.raise_for_status()
        return r.json()

    def cancel_order(self, order_id: int):
        """取消挂单. 50 req/60s."""
        self._throttle("/equity/orders/cancel", 1.5)
        r = httpx.delete(f"{BASE}/equity/orders/{order_id}", headers=self.h, timeout=30)
        r.raise_for_status()
        return True

    def get_order(self, order_id: int):
        """查询单个订单状态 (GET /equity/orders/{id}). 1 req/1s。
        订单已成交/不存在时 T212 返回 404，此处返回 None。"""
        self._throttle("/equity/orders/get", 1.0)
        r = httpx.get(f"{BASE}/equity/orders/{order_id}", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
