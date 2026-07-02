"""Trading212 API 客户端"""
import base64
import math
import time

import httpx

import settings

_BASE_MAP = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}

# T212 限价单（24/5 时段唯一可用的单型）只接受【整股】数量；
# 分数股仅 value-based 市价单支持，但市价单在盘前/盘后/隔夜会被拒。
# 故 24/5 策略统一用整股限价单。_QTY_PRECISION 保留为个别标的的精度覆盖位。
_QTY_PRECISION: dict[str, int] = {}      # ticker -> 小数位覆盖（默认 0=整股）


def _truncate(q: float, decimals: int) -> float:
    """按小数位向下取整（保留符号）：买不超预算、卖不超持仓。"""
    f = 10 ** decimals
    return math.floor(abs(q) * f) / f * (1.0 if q >= 0 else -1.0)


class T212:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 env: str | None = None):
        _key = api_key or settings.T212_API_KEY
        _secret = api_secret or getattr(settings, "T212_API_SECRET", None)
        _env = env or settings.T212_ENV or "demo"
        if not _key:
            raise RuntimeError("T212_API_KEY 未配置")
        # 鉴权模式：
        #   有 secret → Basic Base64(key:secret)
        #   仅 key    → Authorization: <key>（T212 API v0 原始格式）
        if _secret:
            token = base64.b64encode(f"{_key}:{_secret}".encode()).decode()
            self.h = {"Authorization": f"Basic {token}"}
        else:
            self.h = {"Authorization": _key}
        self.base = _BASE_MAP.get(_env, _BASE_MAP["demo"])
        self._last_call: dict[str, float] = {}

    def _throttle(self, path: str, min_interval: float = 2.0):
        now = time.monotonic()
        wait = self._last_call.get(path, 0) + min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_call[path] = time.monotonic()

    def _get(self, path: str, _min_interval: float = 2.0, **params):
        self._throttle(path, _min_interval)
        for attempt in range(4):
            r = httpx.get(f"{self.base}{path}", headers=self.h,
                          params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited: {path}")

    def positions(self):
        # T212 portfolio 限频约 1 req/5s
        return self._get("/equity/positions", _min_interval=5.0)

    def cash(self):
        # account/cash 限频约 1 req/2s
        return self._get("/equity/account/cash", _min_interval=2.0)

    def open_orders(self):
        self._throttle("/equity/orders", 5.0)  # 1 req/5s
        for attempt in range(4):
            r = httpx.get(f"{self.base}/equity/orders", headers=self.h, timeout=30)
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
        """返回全部可交易标的（数量大，建议调用方缓存）"""
        return self._get("/equity/metadata/instruments")

    def live_price(self, ticker: str) -> float:
        """获取标的实时现价（24/5 感知）：持仓 > yfinance(prepost) > Finnhub。"""
        # ① 持仓实时价（T212 支持 24/5）
        try:
            raw = self.positions()
            items = raw if isinstance(raw, list) else raw.get("items", [])
            for p in items:
                if (p.get("instrument") or {}).get("ticker") == ticker:
                    price = float(p.get("currentPrice") or 0)
                    if price > 0:
                        return price
        except Exception:
            pass
        # ② yfinance，启用盘前/盘后
        sym = ticker.split("_")[0]
        try:
            import yfinance as yf
            t = yf.Ticker(sym)
            h = t.history(period="1d", interval="1m", prepost=True, auto_adjust=True)
            closes = h["Close"].dropna()
            if not closes.empty:
                return float(closes.iloc[-1])
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _ok(r):
        """统一错误处理：把 T212 返回的 JSON 错误体一并抛出，便于定位 400/422。"""
        if r.status_code >= 400:
            body = ""
            try:
                body = r.text
            except Exception:
                pass
            raise httpx.HTTPStatusError(
                f"T212 {r.status_code} {r.request.method} {r.request.url}: {body}",
                request=r.request, response=r)
        return r.json()

    def market_order(self, ticker: str, quantity: float):
        """按股数下市价单（quantity>0 买入，quantity<0 卖出）。
        注意：市价单仅在美股常规时段被接受；盘前/盘后/隔夜(24/5)会 400，
        24/5 时段请用 marketable_buy / marketable_sell（可成交限价单）。"""
        self._throttle("/equity/orders/market", 3.0)
        r = httpx.post(f"{self.base}/equity/orders/market", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity}, timeout=30)
        return self._ok(r)

    def market_order_value(self, ticker: str, value: float, current_price: float = 0.0):
        """按金额买入：T212 API 只接受 quantity，用 live_price 实时换算。"""
        if current_price <= 0:
            current_price = self.live_price(ticker)
        if current_price <= 0:
            raise ValueError(f"无法获取 {ticker} 实时现价，无法按金额下单")
        qty = round(value / current_price, 6)
        return self.market_order(ticker, qty)

    def limit_order(self, ticker: str, quantity: float, limit_price: float,
                    time_validity: str = "DAY"):
        """限价单（quantity>0 买入，quantity<0 卖出）。1 req/2s。"""
        self._throttle("/equity/orders/limit", 2.0)
        r = httpx.post(f"{self.base}/equity/orders/limit", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity,
                             "limitPrice": limit_price, "timeValidity": time_validity},
                       timeout=30)
        return self._ok(r)

    def _limit_with_precision(self, ticker: str, raw_qty: float, limit_price: float,
                              time_validity: str):
        """下整股限价单（24/5 唯一可用单型）。raw_qty 带符号（>0 买 <0 卖），
        向下取整为整股：买不超预算、卖不超持仓。单次请求，不试错（避免触发限频）。"""
        dec = _QTY_PRECISION.get(ticker, 0)  # 默认整股；个别标的可在此覆盖
        q = _truncate(raw_qty, dec)
        if abs(q) < 1:
            raise ValueError(f"{ticker} 预算不足 1 股，无法下单（数量 {raw_qty:.4f}）")
        return self.limit_order(ticker, q, limit_price, time_validity)

    def marketable_buy(self, ticker: str, value: float, current_price: float,
                       slippage_pct: float = 0.5, time_validity: str = "DAY"):
        """可成交买入限价单（24/5 友好）：限价 = 现价 ×(1+滑点%)，跨价立即成交。
        按金额换算股数后下限价买单——盘前/盘后/隔夜均可执行，数量精度自适应。"""
        if current_price <= 0:
            current_price = self.live_price(ticker)
        if current_price <= 0:
            raise ValueError(f"无法获取 {ticker} 实时现价，无法按金额下单")
        limit = round(current_price * (1 + slippage_pct / 100), 2)
        raw_qty = value / limit
        return self._limit_with_precision(ticker, raw_qty, limit, time_validity)

    def marketable_sell(self, ticker: str, quantity: float, current_price: float,
                        slippage_pct: float = 0.5, time_validity: str = "DAY"):
        """可成交卖出限价单（24/5 友好）：限价 = 现价 ×(1−滑点%)，跨价立即成交。
        用于止损——盘前/盘后/隔夜均可执行，替代会被拒的市价卖单，数量精度自适应。"""
        if current_price <= 0:
            current_price = self.live_price(ticker)
        if current_price <= 0:
            raise ValueError(f"无法获取 {ticker} 实时现价，无法止损下单")
        limit = round(current_price * (1 - slippage_pct / 100), 2)
        return self._limit_with_precision(ticker, -abs(quantity), limit, time_validity)

    def stop_order(self, ticker: str, quantity: float, stop_price: float,
                   time_validity: str = "DAY"):
        """止损/止盈触发单（quantity>0 买入触发，quantity<0 卖出触发）。1 req/2s。"""
        self._throttle("/equity/orders/stop", 2.0)
        r = httpx.post(f"{self.base}/equity/orders/stop", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity,
                             "stopPrice": stop_price, "timeValidity": time_validity},
                       timeout=30)
        return self._ok(r)

    def cancel_order(self, order_id: int):
        """取消挂单。50 req/60s。"""
        self._throttle("/equity/orders/cancel", 1.5)
        r = httpx.delete(f"{self.base}/equity/orders/{order_id}",
                         headers=self.h, timeout=30)
        r.raise_for_status()
        return True
