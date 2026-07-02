"""量化交易引擎 — tick 级波段策略(源自 lianghua.py,异步多标的版)

挂单状态机(关键)：
  ① 空仓 → 满足买入条件则市价买入，记录买入挂单 id
  ② 等待买入成交(get_order 确认 FILLED)后才进入持仓管理
  ③ 持仓 → 立即挂止盈 sell-limit：限价 = 均价 ×(1+profit_pct%)
     —— 成交价必高于均价；不再用市价卖出止盈
  ④ 轮询该 sell-limit 状态，确认成交后才记录盈亏、回到空仓继续循环
     —— "保证每个挂单确定执行了才继续循环"
  风控：挂止盈期间若浮亏超过 stop_loss%，撤销止盈单并市价止损；
        指标止损(亏损+RSI<35+MACD<0)同样走市价。
  重启对账：持仓但无追踪挂单时，先查 T212 open_orders 接管已有 sell-limit，
            避免重复挂单。

买入: buy_mode=market 空仓即买；buy_mode=ind 时 RSI<rsi_buy 且 MACD diff>0
按金额买入由 T212 client 实时取现价换算为股数(只提交 quantity)。

与 lianghua.py 的差异:
  - 多标的并行,每标的一个 asyncio 任务
  - 配置/成交入库,重启自动恢复 active 策略 + 对账挂单
  - 每日交易次数上限熔断
"""
import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import numpy as np

import settings
from db import get_session
from models import QuantStrategy, QuantTrade
from trades import record_trade

log = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "buy_mode": "turning",  # turning=日内拐点低买(默认); ind=RSI+MACD; market=空仓即市价买
    "rsi_buy": 45,          # RSI 低于此值才允许买入(仅 buy_mode=ind)
    "rsi_sell": 55,         # 保留(signal_stop 辅助参考)
    "stop_loss": 2.0,       # 硬止损 %(持仓亏损超过即强制卖出,无指标限制)
    "profit_pct": 0.5,      # 盈利目标 %(现价高于均价此比例时卖出)
    "budget_ratio": 50.0,   # 每次买入占可用现金的比例 %
    "sell_ratio": 100.0,    # 每次卖出占持仓的比例 %(100=全仓)
    "budget_eur": 1000.0,   # 单笔买入上限(按 currency 计;0=不限,仅靠 budget_ratio)
    "currency": "USD",      # 下单金额币种(默认美元=标的币种,精确无需汇率)
    "interval": 5,          # 检查间隔秒
    "max_trades_day": 50,   # 每日最大成交次数(买+卖),熔断保护
    "slippage_pct": 0.5,    # 24/5 可成交限价单滑点 %(买高卖低跨价,保证盘外即时成交)
    # ── 拐点低买高卖(buy_mode=turning)算法参数 ──
    "turn_tf": "intraday",  # intraday=日内采样高频拐点(默认); daily=日线级 PIP(大波段,低频)
    "turn_window": 180,     # 日内：保留的采样根数(配合 60s 采样 ≈ 3 小时)
    "turn_sample_sec": 60,  # 日内：拐点序列采样间隔(秒)，1 分钟一根，平滑掉 tick 噪声
    "turn_beta": 4,         # 日内：swing 半径(采样根≈分钟)；daily 模式下为 PIP 最小间隔(天)
    "turn_rebound_pct": 0.2,  # 日内：自谷反弹/自峰回落 ≥ 此 % 才确认拐点(过滤噪声)
    "turn_recent": 3,       # 日内：拐点确认后多少根采样内仍触发买入
    "turn_recent_days": 8,  # daily：谷确认后多少个交易日内仍触发买入
    "buy_discount_pct": 0.0,  # 低买折扣 %：buy-limit 挂在现价×(1-此值)，0=就挂现价
    "sell_at_peak": True,   # 高卖目标取算法识别的近期峰价(高于止盈目标时)
    "explain_llm": True,    # 每次触发动作用 LLM 生成一句自然语言解释(best-effort，失败回退规则解释)
}


def _llm_explain(ctx: dict) -> str | None:
    """把一次交易触发的结构化条件交给 LLM，生成一句中文决策解释。best-effort。"""
    if not settings.llm_enabled:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.SILICONFLOW_API_KEY,
                        base_url=settings.SILICONFLOW_BASE_URL, max_retries=0)
        sys_p = ("你是量化交易助手。根据给定的一次交易触发的结构化条件(JSON)，"
                 "用一句中文(不超过60字)解释『基于哪些当前条件、做出了什么动作』，"
                 "措辞专业克制；只依据给定字段，不虚构数据，不加免责声明或多余前后缀。")
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
            temperature=0.3, max_tokens=120, timeout=20)
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        log.warning("quant %s LLM 解释失败: %s", ctx.get("symbol"), e)
        return None

RSI_PERIOD = 14
BB_PERIOD = 20
TICK_WINDOW = 60          # 最近 60 个 tick(5s 间隔 ≈ 5 分钟)
SIGNAL_STOP_RSI = 35      # 指标止损 RSI 阈值

# 进程级共享挂单缓存：open_orders 返回全量订单，多标的策略共用一次拉取，
# 避免每个策略每轮各拉一次撞 T212 限频(1 req/5s)。
_orders_cache: dict = {"ts": 0.0, "data": []}
_orders_lock = asyncio.Lock()
_ORDERS_TTL = 6.0


async def _all_open_orders(force: bool = False) -> list:
    import time as _t
    now = _t.monotonic()
    async with _orders_lock:
        if not force and now - _orders_cache["ts"] < _ORDERS_TTL:
            return _orders_cache["data"]

        def _f():
            from t212.account_cache import get_client
            raw = get_client().open_orders()
            return raw if isinstance(raw, list) else raw.get("items", [])
        try:
            data = await asyncio.to_thread(_f)
            _orders_cache["data"] = data
            _orders_cache["ts"] = now
            return data
        except Exception as e:
            log.warning("open_orders fetch failed: %s", e)
            return _orders_cache["data"] or []


def _invalidate_orders_cache():
    _orders_cache["ts"] = 0.0


class _Indicators:
    """tick 缓存 + RSI(Wilder) / 布林带 / 增量 EMA MACD"""

    def __init__(self):
        self.prices: deque[float] = deque(maxlen=TICK_WINDOW)
        self._ema12: float | None = None
        self._ema26: float | None = None

    def feed(self, price: float):
        if not price or price <= 0:
            return
        p = float(price)
        self.prices.append(p)
        k12, k26 = 2 / 13, 2 / 27
        if self._ema12 is None:
            self._ema12 = self._ema26 = p
        else:
            self._ema12 = p * k12 + self._ema12 * (1 - k12)
            self._ema26 = p * k26 + self._ema26 * (1 - k26)

    def compute(self) -> dict | None:
        if len(self.prices) < max(RSI_PERIOD * 2 + 1, BB_PERIOD):
            return None
        prices = np.array(self.prices)
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        w_g, w_l = gains[-RSI_PERIOD * 2:], losses[-RSI_PERIOD * 2:]
        alpha = 1.0 / RSI_PERIOD
        avg_g, avg_l = w_g[0], w_l[0]
        for g, l in zip(w_g[1:], w_l[1:]):
            avg_g = avg_g * (1 - alpha) + g * alpha
            avg_l = avg_l * (1 - alpha) + l * alpha
        rsi = 100.0 - 100.0 / (1.0 + avg_g / avg_l) if avg_l > 0 else 100.0
        recent = prices[-BB_PERIOD:]
        ma, std = recent.mean(), recent.std()
        macd = (self._ema12 - self._ema26) if self._ema12 is not None else 0.0
        return {"rsi": round(float(rsi), 2),
                "macd_diff": round(float(macd), 4),
                "bb_high": round(float(ma + 2 * std), 2),
                "bb_low": round(float(ma - 2 * std), 2),
                "ticks": len(self.prices)}


class StrategyRunner:
    """单标的策略任务"""

    def __init__(self, symbol: str, t212_ticker: str, params: dict,
                 api_key: str | None = None, api_secret: str | None = None,
                 env: str | None = None, account_id: int | None = None):
        self.symbol = symbol.upper()
        self.ticker = t212_ticker
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.api_key = api_key       # None → 回退到 settings
        self.api_secret = api_secret # None → 仅 api_key 鉴权
        self.env = env
        self.account_id = account_id
        self.ind = _Indicators()
        self.task: asyncio.Task | None = None
        # 运行状态(暴露给 API)
        self.holding = False
        self.qty = 0.0
        self.avg_price = 0.0
        self.last_price = 0.0
        self.last_ind: dict | None = None
        self.trades_today = 0
        self.total_trades = 0
        self.total_pnl = 0.0
        self.wins = 0                 # 本次开启以来盈利平仓次数
        self.losses = 0              # 本次开启以来亏损平仓次数
        self.started_at = datetime.now(timezone.utc)   # 本次开启时间(收益统计基准)
        self.last_check: str | None = None
        self.last_action: str | None = None
        self.last_explain: str | None = None   # 最近一次动作的决策解释(规则/LLM)
        self.error: str | None = None
        self._day = datetime.now(timezone.utc).date()
        # 日内拐点采样序列(独立于 ind tick 窗口；按 turn_sample_sec 采样)
        self._turn_samples: deque[float] = deque(
            maxlen=int(self.params.get("turn_window", 180)))
        self._turn_last_sample = 0.0  # 上次采样的 monotonic 时间
        # 挂单状态机：买入挂单 / 止盈 sell-limit 挂单
        self.buy_order_id: int | None = None
        self.sell_order_id: int | None = None
        self.sell_target: float = 0.0       # 止盈挂单限价
        self.sell_avg: float = 0.0          # 挂止盈时的持仓均价(用于成交后算盈亏)
        self.sell_qty_pending: float = 0.0  # 止盈挂单股数
        self.waiting: str | None = None     # None | 'buy' | 'sell_limit'(供前端展示)
        self._buy_cooldown_until = 0.0      # 资金不足等错误后的买入冷却(monotonic)
        self._client = None                 # 复用 T212 client，使限频节流跨轮持续生效
        self._turn_cache: dict = {"ts": 0.0, "sig": None}  # 拐点信号缓存(日线级,30min)

    def _t212(self):
        # 复用同一实例：T212._throttle 基于实例状态，每次新建会使节流失效→撞 429
        if self._client is None:
            from t212.client import T212
            if self.api_key:
                self._client = T212(api_key=self.api_key,
                                    api_secret=self.api_secret, env=self.env)
            else:
                self._client = T212()
        return self._client

    # ── 数据源 ──
    async def _position(self) -> dict | None:
        def _get():
            raw = self._t212().positions()
            items = raw if isinstance(raw, list) else raw.get("items", [])
            for p in items:
                if p.get("instrument", {}).get("ticker") == self.ticker:
                    return p
            return None
        return await asyncio.to_thread(_get)

    async def _cash(self) -> float:
        def _get():
            return (self._t212().cash() or {}).get("free") or 0.0
        return await asyncio.to_thread(_get)

    async def _quote_price(self) -> float:
        """空仓时取现价（24/5：含盘前/盘后）：yf_symbol > Finnhub。"""
        def _yf():
            try:
                from db import get_session as _gs
                from models import WatchlistItem as _W
                with _gs() as s:
                    row = s.query(_W).filter(
                        _W.symbol == self.symbol, _W.active).first()
                    if row:
                        return (row.symbol_config or {}).get("yf_symbol") or self.symbol
            except Exception:
                pass
            return self.symbol

        yf_sym = await asyncio.to_thread(_yf)

        # yfinance：prepost=True 支持盘前/盘后 24/5 价格
        try:
            import yfinance as yf
            def _dl():
                t = yf.Ticker(yf_sym)
                h = t.history(period="1d", interval="1m",
                              prepost=True, auto_adjust=True)
                closes = h["Close"].dropna()
                return float(closes.iloc[-1]) if not closes.empty else 0.0
            price = await asyncio.to_thread(_dl)
            if price > 0:
                return price
        except Exception:
            pass

        # 降级 Finnhub(仅对美股有效)
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get("https://finnhub.io/api/v1/quote",
                                params={"symbol": self.symbol,
                                        "token": settings.FINNHUB_TOKEN})
                r.raise_for_status()
                return float(r.json().get("c") or 0)
        except Exception:
            return 0.0

    # ── 下单 ──
    def _daily_closes(self, limit: int = 260) -> list[float]:
        """取本标的近 limit 根日线收盘(时间升序)，供拐点识别。"""
        from db import get_session
        from models import Price
        from sqlalchemy import select
        with get_session() as s:
            rows = s.execute(
                select(Price.close).where(Price.symbol == self.symbol,
                                          Price.interval == "1d")
                .order_by(Price.ts.desc()).limit(limit)
            ).scalars().all()
        return [float(c) for c in reversed(rows) if c is not None]

    async def _turning(self) -> dict:
        """拐点信号。
        - intraday(默认)：在日内采样序列上做轻量 swing 拐点，每轮重算(很便宜)。
        - daily：日线级 PIP(论文算法,昂贵)，缓存 30 分钟。
        """
        import time as _t
        tf = self.params.get("turn_tf", "intraday")

        if tf == "intraday":
            from analysis.turning import intraday_turning_signal
            sig = intraday_turning_signal(
                list(self._turn_samples),
                beta=int(self.params.get("turn_beta", 4)),
                rebound_pct=float(self.params.get("turn_rebound_pct", 0.2)),
                recent=int(self.params.get("turn_recent", 3)),
            )
            self._turn_cache = {"ts": _t.monotonic(), "sig": sig}
            return sig

        # daily：缓存 30min，避免每轮重算昂贵 PIP
        if self._turn_cache["sig"] and _t.monotonic() - self._turn_cache["ts"] < 1800:
            return self._turn_cache["sig"]

        def _calc():
            from analysis.turning import turning_signal
            closes = self._daily_closes()
            return turning_signal(
                closes,
                recent_days=int(self.params.get("turn_recent_days", 8)),
                beta=float(self.params.get("turn_beta", 15)),
            )
        try:
            sig = await asyncio.to_thread(_calc)
        except Exception as e:
            log.warning("quant %s 拐点计算失败: %s", self.symbol, e)
            sig = {"signal": "hold", "kind": None, "n": 0}
        self._turn_cache = {"ts": _t.monotonic(), "sig": sig}
        return sig

    async def _buy_limit(self, value_eur: float, limit_price: float) -> dict | None:
        """挂买入限价单(GTC)：拐点『低买』入场。整股向下取整；24/5 限价单盘外也能挂。
        limit_price 即买入价(≤ 此价成交)，由调用方按现价/谷位给定。"""
        def _do():
            cli = self._t212()
            px = float(limit_price)
            if px <= 0:
                raise ValueError(f"无效限价 {px}")
            qty = int(value_eur // px)
            if qty < 1:
                raise ValueError(f"预算 {value_eur:.2f} 不足 1 股（限价 {px:.4f}）")
            return cli.limit_order(self.ticker, qty, round(px, 2), "GOOD_TILL_CANCEL")
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            msg = str(e)
            if "insufficient-free-for-stocks-buy" in msg or "Insufficient funds" in msg:
                self._buy_cooldown_until = time.monotonic() + 600
                self.error = "资金不足，已暂停买入 10 分钟"
            elif "429" in msg or "TooManyRequests" in msg:
                self._buy_cooldown_until = time.monotonic() + 120
                self.error = "T212 限流，买入冷却 2 分钟"
            else:
                self._buy_cooldown_until = time.monotonic() + 60
                self.error = f"挂买入限价失败: {e}"
            log.warning("quant %s buy-limit 失败: %s", self.symbol, e)
            return None

    async def _buy(self, value_eur: float, price: float = 0.0) -> dict | None:
        """市价买入：立即按当前市场价成交（不挂会悬空的 buy-limit）。整股向下取整，
        避免低价股 quantity-precision-mismatch。市价单仅常规时段可用（盘外 T212 拒单），
        持仓后由 24/5 止盈/止损限价单保护。成交均价以 T212 持仓 averagePricePaid 为准。"""
        def _do():
            cli = self._t212()
            px = price if price > 0 else cli.live_price(self.ticker)
            if px <= 0:
                raise ValueError(f"无法获取 {self.ticker} 现价，无法买入")
            qty = int(value_eur // px)          # 整股向下取整：不超预算
            if qty < 1:
                raise ValueError(f"预算 {value_eur:.2f} 不足 1 股（现价 {px:.4f}）")
            return cli.market_order(self.ticker, qty)
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            msg = str(e)
            if "insufficient-free-for-stocks-buy" in msg or "Insufficient funds" in msg \
                    or "现金不足" in msg:
                self._buy_cooldown_until = time.monotonic() + 600
                self.error = "资金不足，已暂停买入 10 分钟"
                log.warning("quant %s 资金不足，买入冷却 10 分钟", self.symbol)
            elif "TooManyRequests" in msg or "too many requests" in msg or "429" in msg:
                self._buy_cooldown_until = time.monotonic() + 120
                self.error = "T212 限流，买入冷却 2 分钟"
                log.warning("quant %s 触发限流，买入冷却 2 分钟", self.symbol)
            else:
                # 其它失败（如精度/参数）：冷却 60s，避免每个 interval 反复打 T212
                self._buy_cooldown_until = time.monotonic() + 60
                self.error = f"买入失败: {e}"
                log.warning("quant %s 买入失败: %s", self.symbol, e)
            return None

    async def _sell(self, quantity: float, price: float = 0.0) -> dict | None:
        """24/5 止损卖出：可成交限价单（限价=现价×(1−滑点%)），盘前/盘后/隔夜均可执行。"""
        slip = self.params.get("slippage_pct", 0.5)
        def _do():
            return self._t212().marketable_sell(
                self.ticker, abs(quantity), price, slippage_pct=slip)
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s sell failed: %s", self.symbol, e)
            self.error = f"卖出失败: {e}"
            return None

    async def _sell_limit(self, quantity: float, limit_price: float) -> dict | None:
        """挂止盈 sell-limit 单 (GTC)，数量精度自适应。"""
        def _do():
            return self._t212()._limit_with_precision(
                self.ticker, -abs(quantity), limit_price, "GOOD_TILL_CANCEL")
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s sell-limit failed: %s", self.symbol, e)
            self.error = f"挂止盈单失败: {e}"
            return None

    async def _get_order(self, order_id: int) -> dict | None:
        def _do():
            orders = self._t212().open_orders()
            items = orders if isinstance(orders, list) else orders.get("items", [])
            for o in items:
                if o.get("id") == order_id:
                    return o
            return None
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s get_order(%s) failed: %s", self.symbol, order_id, e)
            return None

    async def _cancel(self, order_id: int) -> bool:
        def _do():
            return self._t212().cancel_order(order_id)
        try:
            ok = bool(await asyncio.to_thread(_do))
            _invalidate_orders_cache()
            return ok
        except Exception as e:
            log.warning("quant %s cancel(%s) failed: %s", self.symbol, order_id, e)
            return False

    async def _reconcile_orders(self) -> tuple[dict | None, dict | None]:
        """对账本标的的 T212 挂单，强制「买入挂单 ≤1、卖出挂单 ≤1」：
        多出的同向挂单一律撤销（自愈历史重复单），返回 (买入挂单, 卖出挂单)。
        共用进程级缓存的全量挂单，避免多策略撞限频。"""
        items = await _all_open_orders()
        buys, sells = [], []
        for o in items:
            tk = o.get("ticker") or (o.get("instrument") or {}).get("ticker")
            if tk != self.ticker:
                continue
            q = o.get("quantity") or 0
            (buys if q > 0 else sells).append(o)

        # 多余的同向挂单全部撤掉，各侧只保留一个
        extras = buys[1:] + sells[1:]
        for extra in extras:
            oid = extra.get("id")
            if oid:
                log.warning("quant %s 撤销重复挂单 %s", self.symbol, oid)
                await self._cancel(oid)
        if extras:
            _invalidate_orders_cache()  # 撤单后下次重新拉取
        return (buys[0] if buys else None, sells[0] if sells else None)

    @staticmethod
    def _order_filled(od: dict | None) -> bool:
        """订单是否已成交。od=None(404) 视为已成交/已消失。"""
        if od is None:
            return True
        status = (od.get("status") or "").upper()
        if status == "FILLED":
            return True
        fq = od.get("filledQuantity") or 0
        oq = abs(od.get("quantity") or 0)
        return oq > 0 and abs(fq) >= oq

    @staticmethod
    def _order_open(od: dict | None) -> bool:
        """订单是否仍在挂（未进入终态）。"""
        if od is None:
            return False
        status = (od.get("status") or "").upper()
        return status not in ("FILLED", "CANCELLED", "CANCELED",
                              "REJECTED", "EXPIRED", "ERROR")

    # ── 记录 ──
    def _record(self, side: str, reason: str, qty: float, price: float,
                pnl: float | None, order: dict | None, count: bool = True):
        """记录一笔成交/挂单到 DB。count=False 时只入库+提示，不计入
        当日成交次数与累计盈亏（用于挂单动作本身，区别于真正成交）。
        每次动作都会附带一句『基于当前条件→动作』的决策解释（规则生成，可选 LLM 增强）。"""
        ctx = self._decision_context(reason, side, price, pnl)
        explain = self._rule_explain(ctx)
        detail = {**(self.last_ind or {}), "ctx": ctx, "explain": explain}
        tid = None
        with get_session() as s:
            trade = QuantTrade(
                symbol=self.symbol, t212_ticker=self.ticker, side=side,
                reason=reason, quantity=qty, price=price, pnl=pnl,
                order_id=str((order or {}).get("id", "")),
                detail=detail)
            s.add(trade)
            s.flush()
            tid = trade.id
        if count:
            self.trades_today += 1
            self.total_trades += 1
            if pnl is not None:
                self.total_pnl += pnl
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
        self.last_explain = explain
        self.last_action = (f"{side} {qty:.4f}股 @ {price:.2f} ({reason})"
                            + (f" pnl={pnl:+.2f}" if pnl is not None else ""))
        # 统一交易历史（与手动下单同表）
        record_trade(
            source="quant", side=("sell" if side == "sell_limit" else side),
            symbol=self.symbol, t212_ticker=self.ticker,
            order_type="limit",  # 全部为限价类（止盈 sell-limit + 买入/止损可成交限价，24/5）
            quantity=qty, price=price, pnl=pnl, reason=reason,
            currency=(self.params.get("currency", "USD") if side == "buy" else None),
            status=("submitted" if reason == "set_profit_limit" else "filled"),
            order_id=(order or {}).get("id"), detail=detail)
        asyncio.ensure_future(
            self._enrich_and_notify(tid, side, reason, qty, price, pnl, ctx, explain))

    # ── 决策解释 ──────────────────────────────────────────────────────────────
    def _decision_context(self, reason: str, side: str, price: float,
                          pnl: float | None) -> dict:
        """采集触发时的判断依据（价格 / 指标 / 拐点 / 浮盈亏 vs 阈值）。"""
        ind = self.last_ind or {}
        turn = self._turn_cache.get("sig") or {}
        p = self.params
        gain = None
        base = price if (side != "buy" and price > 0) else self.last_price
        if self.avg_price > 0 and base > 0 and side != "buy":
            gain = round((price - self.avg_price) / self.avg_price * 100, 2)
        return {
            "symbol": self.symbol, "reason": reason, "side": side,
            "price": round(price, 4),
            "avg_price": round(self.avg_price, 4) if self.avg_price else None,
            "gain_pct": gain, "pnl": round(pnl, 2) if pnl is not None else None,
            "buy_mode": p.get("buy_mode"),
            "rsi": ind.get("rsi"), "macd_diff": ind.get("macd_diff"),
            "bb_low": ind.get("bb_low"), "bb_high": ind.get("bb_high"),
            "turn_signal": turn.get("signal"), "turn_kind": turn.get("kind"),
            "valley_px": turn.get("valley_px"), "peak_px": turn.get("peak_px"),
            "rebound_pct": p.get("turn_rebound_pct"),
            "rsi_buy": p.get("rsi_buy"), "profit_pct": p.get("profit_pct"),
            "stop_loss": p.get("stop_loss"),
        }

    @staticmethod
    def _rule_explain(c: dict) -> str:
        """确定性规则解释：根据条件说明为何做此动作（LLM 不可用时的可靠兜底）。"""
        r = c["reason"]
        px = c["price"]
        g = lambda v, suf="": "—" if v is None else f"{v}{suf}"
        if r == "turning_buy":
            vp = c["valley_px"]
            reb = f"自近期谷 {vp} 反弹≥{g(c['rebound_pct'])}%" if vp else "日内出现谷底转向"
            return (f"拐点低买：现价 {px} {reb}，判定日内低位转向；"
                    f"RSI {g(c['rsi'])}、MACD {g(c['macd_diff'])} → 挂 buy-limit 低买。")
        if r == "market_buy":
            return f"空仓即买：市价模式且当前空仓，直接按现价 {px} 建仓。"
        if r == "ind_buy":
            return (f"指标买入：RSI {g(c['rsi'])} 低于阈值 {g(c['rsi_buy'])}（超卖）且 "
                    f"MACD diff {g(c['macd_diff'])}>0（动能转正）→ 按现价 {px} 买入。")
        if r == "set_profit_limit":
            tp = c["peak_px"]
            extra = f"，并不低于算法峰价 {tp}" if (tp and c["buy_mode"] == "turning") else ""
            return (f"挂止盈：持仓均价 {g(c['avg_price'])}，按 +{g(c['profit_pct'])}% 设高卖目标 "
                    f"{px}{extra}，等待成交。")
        if r == "profit_limit":
            return (f"止盈成交：卖出 @ {px} 达到均价 {g(c['avg_price'])} +{g(c['profit_pct'])}% 目标，"
                    f"本笔盈利 {g(c['pnl'])} → 完成一轮低买高卖。")
        if r == "hard_stop":
            return (f"硬止损：浮亏 {g(c['gain_pct'], '%')} 触及 -{g(c['stop_loss'])}% 止损线，"
                    f"撤止盈并按现价 {px} 市价止损，控制回撤。")
        if r == "signal_stop":
            return (f"指标止损：浮亏 {g(c['gain_pct'], '%')} 且 RSI {g(c['rsi'])}<35 超卖、"
                    f"MACD {g(c['macd_diff'])}<0 下行，趋势走弱 → @ {px} 止损离场。")
        return f"{c['side']} @ {px}（{r}）。"

    async def _enrich_and_notify(self, tid, side, reason, qty, price, pnl,
                                 ctx: dict, explain: str):
        """成交类动作可选用 LLM 生成更自然的解释；随后推送通知。"""
        final = explain
        real = reason in ("turning_buy", "market_buy", "ind_buy",
                          "profit_limit", "hard_stop", "signal_stop")
        if self.params.get("explain_llm", True) and real:
            llm = await asyncio.to_thread(_llm_explain, ctx)
            if llm:
                final = llm
                self.last_explain = llm
                if tid is not None:
                    try:
                        with get_session() as s:
                            t = s.get(QuantTrade, tid)
                            if t:
                                d = dict(t.detail or {})
                                d["explain_llm"] = llm
                                t.detail = d
                    except Exception as e:
                        log.warning("quant %s 更新 LLM 解释失败: %s", self.symbol, e)
        await self._notify(side, reason, qty, price, pnl, final)

    async def _notify(self, side, reason, qty, price, pnl, explain=None):
        if not (settings.telegram_enabled and settings.TELEGRAM_CHAT_ID):
            return
        try:
            from notify.telegram import TelegramSender
            emoji = "🟢" if side == "buy" else "🔴"
            txt = (f"{emoji} <b>量化 {self.symbol}</b> {side.upper()} "
                   f"{qty:.4f}股 @ {price:.2f}\n"
                   f"原因: {reason}"
                   + (f" · 盈亏 {pnl:+.2f}" if pnl is not None else "")
                   + (f"\n💡 {explain}" if explain else "")
                   + f"\n今日 {self.trades_today} 笔 · 累计盈亏 {self.total_pnl:+.2f}")
            await TelegramSender().send(settings.TELEGRAM_CHAT_ID, txt)
        except Exception as e:
            log.warning("quant notify failed: %s", e)

    def _reset_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self.trades_today = 0

    # ── 主循环 ──
    async def run(self):
        p = self.params
        log.info("quant %s started: %s env=%s account=%s params=%s",
                 self.symbol, self.ticker, self.env or settings.T212_ENV,
                 self.account_id, p)
        while True:
            try:
                self._reset_day()
                self.last_check = datetime.now(timezone.utc).isoformat()
                self.error = None

                pos = await self._position()
                if pos:
                    self.holding = True
                    self.qty = float(pos.get("quantity") or 0)
                    self.avg_price = float(pos.get("averagePricePaid") or 0)
                    price = float(pos.get("currentPrice") or 0)
                else:
                    if self.holding:
                        self.holding = False
                        self.qty = 0.0
                    price = await self._quote_price()

                if price > 0:
                    self.last_price = price
                    self.ind.feed(price)
                    # 拐点采样：每 turn_sample_sec 取一根，喂日内拐点序列
                    sample_sec = max(5, int(self.params.get("turn_sample_sec", 60)))
                    if time.monotonic() - self._turn_last_sample >= sample_sec:
                        self._turn_samples.append(price)
                        self._turn_last_sample = time.monotonic()
                self.last_ind = self.ind.compute()
                ind = self.last_ind

                # 对账 T212 挂单：强制买/卖各 ≤1，多余撤销（权威状态来源）
                buy_ord, sell_ord = await self._reconcile_orders()

                # 已持仓却仍挂着买入单 → 必是残留,撤掉以便挂止盈保护
                if pos and buy_ord is not None:
                    await self._cancel(buy_ord.get("id"))
                    log.warning("quant %s 持仓中撤销残留买入挂单 %s",
                                self.symbol, buy_ord.get("id"))
                    buy_ord = None
                    self.buy_order_id = None

                # ===== 状态①：已有买入挂单 → 等待成交，绝不重复下单 =====
                if buy_ord is not None:
                    self.buy_order_id = buy_ord.get("id")
                    self.waiting = "buy"
                    await asyncio.sleep(p["interval"])
                    continue
                self.buy_order_id = None

                # ===== 状态②：已有止盈卖出挂单 → 监控 + 硬止损保护 =====
                if sell_ord is not None:
                    self.sell_order_id = sell_ord.get("id")
                    self.sell_target = float(sell_ord.get("limitPrice") or self.sell_target or 0)
                    self.sell_qty_pending = abs(float(sell_ord.get("quantity") or self.sell_qty_pending or 0))
                    if not self.sell_avg and self.avg_price > 0:
                        self.sell_avg = self.avg_price
                    self.waiting = "sell_limit"
                    # 浮亏超阈值 → 撤止盈单转市价止损
                    if self.avg_price > 0 and price > 0 \
                            and (price - self.avg_price) / self.avg_price * 100 <= -p["stop_loss"]:
                        await self._cancel(self.sell_order_id)
                        sq = round(self.qty * p["sell_ratio"] / 100, 4)
                        ms = await self._sell(sq, price)
                        if ms:
                            pnl = sq * (price - self.avg_price)
                            self._record("sell", "hard_stop", sq, price, pnl, ms)
                            self.holding = p["sell_ratio"] < 100
                        self.sell_order_id = None
                        self.sell_target = self.sell_avg = self.sell_qty_pending = 0.0
                    await asyncio.sleep(p["interval"])
                    continue

                # 卖出挂单消失但之前有 → 视为止盈成交,记录盈亏并回到空仓
                if self.sell_order_id is not None:
                    pnl = self.sell_qty_pending * (self.sell_target - self.sell_avg)
                    self._record("sell", "profit_limit", self.sell_qty_pending,
                                 self.sell_target, pnl, {"id": self.sell_order_id})
                    log.info("quant %s 止盈挂单 %s 成交 @ %.4f pnl=%.2f",
                             self.symbol, self.sell_order_id, self.sell_target, pnl)
                    self.sell_order_id = None
                    self.sell_target = self.sell_avg = self.sell_qty_pending = 0.0
                    self.holding = False
                    self.waiting = None
                    await asyncio.sleep(p["interval"])
                    continue

                self.waiting = None

                # 每日熔断（仅限制新开买入）
                if self.trades_today >= p["max_trades_day"]:
                    await asyncio.sleep(p["interval"])
                    continue

                # ===== 状态③：持仓但无止盈挂单 → 按 profit_pct 在均价之上挂 sell-limit =====
                if self.holding and self.qty > 0 and self.avg_price > 0:
                    gain_pct = (price - self.avg_price) / self.avg_price * 100 if price > 0 else 0.0
                    sell_qty = round(self.qty * p["sell_ratio"] / 100, 4)

                    # 已深亏 → 直接可成交限价硬止损（24/5），不挂止盈
                    if price > 0 and gain_pct <= -p["stop_loss"]:
                        ms = await self._sell(sell_qty, price)
                        if ms:
                            pnl = sell_qty * (price - self.avg_price)
                            self._record("sell", "hard_stop", sell_qty, price, pnl, ms)
                            self.holding = p["sell_ratio"] < 100
                        await asyncio.sleep(3)
                        continue

                    # 指标止损（亏损 + RSI 超卖 + MACD 下行）→ 可成交限价（24/5）
                    if ind and gain_pct < 0 and ind["rsi"] < SIGNAL_STOP_RSI \
                            and ind["macd_diff"] < 0:
                        ms = await self._sell(sell_qty, price)
                        if ms:
                            pnl = sell_qty * (price - self.avg_price)
                            self._record("sell", "signal_stop", sell_qty, price, pnl, ms)
                            self.holding = p["sell_ratio"] < 100
                        await asyncio.sleep(3)
                        continue

                    # 挂止盈 sell-limit：基准 = 均价 ×(1+profit_pct%)(止盈下限)。
                    # turning 模式下若算法识别的近期峰价更高且开启 sell_at_peak，
                    # 则高卖目标抬到峰价(算法决定卖点，赚更多)，止盈%充当下限。
                    target = round(self.avg_price * (1 + p["profit_pct"] / 100), 2)
                    if p.get("buy_mode") == "turning" and p.get("sell_at_peak", True):
                        peak = (self._turn_cache.get("sig") or {}).get("peak_px")
                        if peak and peak > target:
                            target = round(float(peak), 2)
                    order = await self._sell_limit(sell_qty, target)
                    if order and order.get("id"):
                        self.sell_order_id = order["id"]
                        self.sell_target = target
                        self.sell_avg = self.avg_price
                        self.sell_qty_pending = sell_qty
                        self._record("sell_limit", "set_profit_limit", sell_qty,
                                     target, None, order, count=False)
                        _invalidate_orders_cache()
                        log.info("quant %s 已挂止盈 %s @ %.4f (均价 %.4f +%.2f%%)",
                                 self.symbol, self.sell_order_id, target,
                                 self.avg_price, p["profit_pct"])
                    await asyncio.sleep(p["interval"])
                    continue

                # ===== 状态④：空仓且无买入挂单 → 评估买入(市价单，立即按市价成交) =====
                if not self.holding and price > 0 \
                        and time.monotonic() >= self._buy_cooldown_until:
                    buy_mode = p.get("buy_mode", "ind")
                    should_buy = False
                    buy_reason = "ind_buy"
                    use_limit = False        # turning 模式用 buy-limit 低买

                    if buy_mode == "market":
                        should_buy = True
                        buy_reason = "market_buy"
                    elif buy_mode == "ind" and ind:
                        if ind["rsi"] < p["rsi_buy"] and ind["macd_diff"] > 0:
                            should_buy = True
                    elif buy_mode == "turning":
                        # 拐点择时：仅在近期确认『谷』(低位刚转向)时买入，挂 buy-limit 低买
                        sig = await self._turning()
                        self.last_action = (f"拐点信号 {sig.get('signal')}"
                                            f"({sig.get('kind') or '-'}, {sig.get('days_since')}d前)")
                        if sig.get("signal") == "buy":
                            should_buy = True
                            use_limit = True
                            buy_reason = "turning_buy"

                    if should_buy:
                        cash = await self._cash()
                        value = cash * p["budget_ratio"] / 100
                        if p.get("budget_eur", 0) > 0:
                            value = min(value, p["budget_eur"])
                        if value >= 1:
                            if use_limit:
                                # 低买：限价挂在 现价×(1-低买折扣%)，≤ 此价成交，控制入场成本
                                disc = float(p.get("buy_discount_pct", 0) or 0) / 100
                                order = await self._buy_limit(value, price * (1 - disc))
                            else:
                                order = await self._buy(value, price)
                            if order and order.get("id"):
                                est_qty = float(int(value / price))  # 整股，与实际下单一致
                                self._record("buy", buy_reason, est_qty, price, None, order)
                                self.buy_order_id = order["id"]
                                self.waiting = "buy"
                                _invalidate_orders_cache()
                                await asyncio.sleep(3)
                                continue
                        else:
                            self.error = f"现金不足: {cash:.2f}"

                await asyncio.sleep(p["interval"])
            except asyncio.CancelledError:
                log.info("quant %s stopped", self.symbol)
                raise
            except Exception as e:
                log.warning("quant %s loop error: %s", self.symbol, e)
                self.error = str(e)
                await asyncio.sleep(p["interval"])

    def status(self) -> dict:
        gain_pct = None
        if self.holding and self.avg_price > 0 and self.last_price > 0:
            gain_pct = round(
                (self.last_price - self.avg_price) / self.avg_price * 100, 2)
        return {
            "symbol": self.symbol, "t212_ticker": self.ticker,
            "running": bool(self.task and not self.task.done()),
            "env": self.env or settings.T212_ENV,
            "account_id": self.account_id,
            "params": self.params,
            "holding": self.holding, "quantity": self.qty,
            "avg_price": self.avg_price, "last_price": self.last_price,
            "gain_pct": gain_pct,
            "indicators": self.last_ind,
            "trades_today": self.trades_today,
            "total_trades": self.total_trades,
            "total_pnl": round(self.total_pnl, 2),
            # ── 本次开启以来的收益统计(方便灵活调整) ──
            "started_at": self.started_at.isoformat(),
            "realized_pnl": round(self.total_pnl, 2),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": (round(self.wins / (self.wins + self.losses) * 100, 1)
                         if (self.wins + self.losses) else None),
            "roi_pct": (round(self.total_pnl / self.params["budget_eur"] * 100, 2)
                        if self.params.get("budget_eur", 0) > 0 else None),
            "last_check": self.last_check,
            "last_action": self.last_action,
            "last_explain": self.last_explain,
            "error": self.error,
            # 挂单状态机
            "waiting": self.waiting,                 # None|'buy'|'sell_limit'
            "sell_order_id": self.sell_order_id,
            "sell_target": round(self.sell_target, 4) if self.sell_target else None,
            "sell_qty_pending": self.sell_qty_pending or None,
            # 拐点信号(turning 模式)：buy=谷/可买, sell=峰, hold=观望
            "turn_signal": (self._turn_cache.get("sig") or {}).get("signal"),
        }


# ── 引擎管理 ──────────────────────────────────────────────────────────────────

_runners: dict[str, StrategyRunner] = {}


def get_runner(symbol: str) -> StrategyRunner | None:
    return _runners.get(symbol.upper())


def list_runners() -> list[StrategyRunner]:
    return list(_runners.values())


def start(symbol: str, t212_ticker: str, params: dict,
          account_id: int | None = None,
          api_key: str | None = None,
          api_secret: str | None = None,
          env: str | None = None) -> StrategyRunner:
    sym = symbol.upper()
    old = _runners.get(sym)
    if old and old.task and not old.task.done():
        old.task.cancel()
    runner = StrategyRunner(sym, t212_ticker, params,
                            api_key=api_key, api_secret=api_secret,
                            env=env, account_id=account_id)
    runner.task = asyncio.create_task(runner.run())
    _runners[sym] = runner
    with get_session() as s:
        row = s.get(QuantStrategy, sym)
        if row:
            row.t212_ticker = t212_ticker
            row.params = runner.params
            row.active = True
            row.account_id = account_id
        else:
            s.add(QuantStrategy(symbol=sym, t212_ticker=t212_ticker,
                                params=runner.params, active=True,
                                account_id=account_id))
    return runner


def stop(symbol: str) -> bool:
    sym = symbol.upper()
    runner = _runners.get(sym)
    stopped = False
    if runner and runner.task and not runner.task.done():
        runner.task.cancel()
        stopped = True
    with get_session() as s:
        row = s.get(QuantStrategy, sym)
        if row:
            row.active = False
    return stopped


def resume_active():
    """启动时恢复 active=True 的策略（携带账户 API key）"""
    with get_session() as s:
        rows = s.query(QuantStrategy).filter(
            QuantStrategy.active.is_(True)).all()
        configs = []
        for r in rows:
            api_key = None
            api_secret = None
            env = None
            if r.account_id:
                from models import T212Account
                acct = s.get(T212Account, r.account_id)
                if acct:
                    api_key = acct.api_key
                    api_secret = acct.api_secret
                    env = acct.env
            configs.append((r.symbol, r.t212_ticker, dict(r.params or {}),
                             r.account_id, api_key, api_secret, env))
    for sym, ticker, params, account_id, api_key, api_secret, env in configs:
        start(sym, ticker, params, account_id=account_id,
              api_key=api_key, api_secret=api_secret, env=env)
        log.info("quant resumed: %s (account=%s)", sym, account_id)
    return len(configs)


def shutdown():
    for r in _runners.values():
        if r.task and not r.task.done():
            r.task.cancel()
