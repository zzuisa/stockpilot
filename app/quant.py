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
import logging
from collections import deque
from datetime import datetime, timezone

import httpx
import numpy as np

import settings
from db import get_session
from models import QuantStrategy, QuantTrade

log = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "buy_mode": "ind",      # "ind"=RSI+MACD信号买入; "market"=空仓即市价买入
    "rsi_buy": 45,          # RSI 低于此值才允许买入(仅 buy_mode=ind)
    "rsi_sell": 55,         # 保留(signal_stop 辅助参考)
    "stop_loss": 1.0,       # 硬止损 %(持仓亏损超过即强制卖出,无指标限制)
    "profit_pct": 2.0,      # 盈利目标 %(现价高于均价此比例时卖出)
    "budget_ratio": 50.0,   # 每次买入占可用现金的比例 %
    "sell_ratio": 100.0,    # 每次卖出占持仓的比例 %(100=全仓)
    "budget_eur": 0.0,      # 单笔买入上限 €(0=不限,仅靠 budget_ratio)
    "interval": 5,          # 检查间隔秒
    "max_trades_day": 10,   # 每日最大成交次数(买+卖),熔断保护
}

RSI_PERIOD = 14
BB_PERIOD = 20
TICK_WINDOW = 60          # 最近 60 个 tick(5s 间隔 ≈ 5 分钟)
SIGNAL_STOP_RSI = 35      # 指标止损 RSI 阈值


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

    def __init__(self, symbol: str, t212_ticker: str, params: dict):
        self.symbol = symbol.upper()
        self.ticker = t212_ticker
        self.params = {**DEFAULT_PARAMS, **(params or {})}
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
        self.last_check: str | None = None
        self.last_action: str | None = None
        self.error: str | None = None
        self._day = datetime.now(timezone.utc).date()
        # 挂单状态机：买入挂单 / 止盈 sell-limit 挂单
        self.buy_order_id: int | None = None
        self.sell_order_id: int | None = None
        self.sell_target: float = 0.0       # 止盈挂单限价
        self.sell_avg: float = 0.0          # 挂止盈时的持仓均价(用于成交后算盈亏)
        self.sell_qty_pending: float = 0.0  # 止盈挂单股数
        self.waiting: str | None = None     # None | 'buy' | 'sell_limit'(供前端展示)

    # ── 数据源 ──
    async def _position(self) -> dict | None:
        from t212.client import T212
        def _get():
            raw = T212().positions()
            items = raw if isinstance(raw, list) else raw.get("items", [])
            for p in items:
                if p.get("instrument", {}).get("ticker") == self.ticker:
                    return p
            return None
        return await asyncio.to_thread(_get)

    async def _cash(self) -> float:
        from t212.client import T212
        def _get():
            return (T212().cash() or {}).get("free") or 0.0
        return await asyncio.to_thread(_get)

    async def _quote_price(self) -> float:
        """空仓时取现价:优先 yf_symbol(兼容德股等非美股),降级 Finnhub"""
        # 从 DB 取 yf_symbol 覆盖(如 2DG→2DG.SG)
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

        # 先用 yfinance(支持全球交易所)
        try:
            import yfinance as yf
            def _dl():
                t = yf.Ticker(yf_sym)
                h = t.history(period="1d", interval="1m", auto_adjust=True)
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
    async def _buy(self, value_eur: float) -> dict | None:
        from t212.client import T212
        def _do():
            # market_order_value 内部会实时取现价换算成股数后只用 quantity 下单
            return T212().market_order_value(self.ticker, round(value_eur, 2))
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s buy failed: %s", self.symbol, e)
            self.error = f"买入失败: {e}"
            return None

    async def _sell(self, quantity: float) -> dict | None:
        from t212.client import T212
        def _do():
            return T212().market_order(self.ticker, -abs(quantity))
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s sell failed: %s", self.symbol, e)
            self.error = f"卖出失败: {e}"
            return None

    async def _sell_limit(self, quantity: float, limit_price: float) -> dict | None:
        """挂止盈 sell-limit 单 (GTC)。"""
        from t212.client import T212
        def _do():
            return T212().limit_order(self.ticker, -abs(quantity),
                                      limit_price, "GOOD_TILL_CANCEL")
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s sell-limit failed: %s", self.symbol, e)
            self.error = f"挂止盈单失败: {e}"
            return None

    async def _get_order(self, order_id: int) -> dict | None:
        from t212.client import T212
        def _do():
            return T212().get_order(order_id)
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s get_order(%s) failed: %s", self.symbol, order_id, e)
            return None

    async def _cancel(self, order_id: int) -> bool:
        from t212.client import T212
        def _do():
            return T212().cancel_order(order_id)
        try:
            return bool(await asyncio.to_thread(_do))
        except Exception as e:
            log.warning("quant %s cancel(%s) failed: %s", self.symbol, order_id, e)
            return False

    async def _open_sell_limit(self) -> dict | None:
        """查 T212 当前是否已有本标的的 sell-limit 挂单（重启后对账，避免重复挂单）。"""
        from t212.client import T212
        def _do():
            raw = T212().open_orders()
            items = raw if isinstance(raw, list) else raw.get("items", [])
            for o in items:
                tk = o.get("ticker") or (o.get("instrument") or {}).get("ticker")
                otype = (o.get("type") or o.get("orderType") or "").upper()
                qty = o.get("quantity") or 0
                if tk == self.ticker and otype == "LIMIT" and qty < 0:
                    return o
            return None
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            log.warning("quant %s open_orders check failed: %s", self.symbol, e)
            return None

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
        当日成交次数与累计盈亏（用于挂单动作本身，区别于真正成交）。"""
        with get_session() as s:
            s.add(QuantTrade(
                symbol=self.symbol, t212_ticker=self.ticker, side=side,
                reason=reason, quantity=qty, price=price, pnl=pnl,
                order_id=str((order or {}).get("id", "")),
                detail=self.last_ind))
        if count:
            self.trades_today += 1
            self.total_trades += 1
            if pnl is not None:
                self.total_pnl += pnl
        self.last_action = (f"{side} {qty:.4f}股 @ {price:.2f} ({reason})"
                            + (f" pnl={pnl:+.2f}" if pnl is not None else ""))
        asyncio.ensure_future(self._notify(side, reason, qty, price, pnl))

    async def _notify(self, side, reason, qty, price, pnl):
        if not (settings.telegram_enabled and settings.TELEGRAM_CHAT_ID):
            return
        try:
            from notify.telegram import TelegramSender
            emoji = "🟢" if side == "buy" else "🔴"
            txt = (f"{emoji} <b>量化 {self.symbol}</b> {side.upper()} "
                   f"{qty:.4f}股 @ {price:.2f}\n"
                   f"原因: {reason}"
                   + (f" · 盈亏 {pnl:+.2f}" if pnl is not None else "")
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
        log.info("quant %s started: %s env=%s params=%s",
                 self.symbol, self.ticker, settings.T212_ENV, p)
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
                self.last_ind = self.ind.compute()
                ind = self.last_ind

                # ===== 状态①：等待买入挂单成交（确认成交才继续）=====
                if self.buy_order_id is not None:
                    self.waiting = "buy"
                    od = await self._get_order(self.buy_order_id)
                    if self._order_filled(od) or pos:
                        log.info("quant %s 买入挂单 %s 已成交", self.symbol, self.buy_order_id)
                        self.buy_order_id = None
                        self.waiting = None
                    elif not self._order_open(od):
                        log.warning("quant %s 买入挂单 %s 未成交(status=%s)",
                                    self.symbol, self.buy_order_id, (od or {}).get("status"))
                        self.buy_order_id = None
                        self.waiting = None
                    await asyncio.sleep(p["interval"])
                    continue

                # ===== 状态②：监控止盈 sell-limit 挂单（成交确认前不做新动作）=====
                if self.sell_order_id is not None:
                    self.waiting = "sell_limit"
                    od = await self._get_order(self.sell_order_id)
                    # 已成交 → 记录止盈盈亏（成交价必高于均价），回到空仓
                    if self._order_filled(od):
                        pnl = self.sell_qty_pending * (self.sell_target - self.sell_avg)
                        self._record("sell", "profit_limit", self.sell_qty_pending,
                                     self.sell_target, pnl, od or {"id": self.sell_order_id})
                        log.info("quant %s 止盈挂单 %s 成交 @ %.4f pnl=%.2f",
                                 self.symbol, self.sell_order_id, self.sell_target, pnl)
                        self.sell_order_id = None
                        self.sell_target = self.sell_avg = self.sell_qty_pending = 0.0
                        self.holding = False
                        self.waiting = None
                        await asyncio.sleep(p["interval"])
                        continue
                    # 仍挂单 → 保留硬止损保护：亏损超阈值则撤止盈单转市价止损
                    if self._order_open(od):
                        if self.avg_price > 0 and price > 0:
                            gp = (price - self.avg_price) / self.avg_price * 100
                            if gp <= -p["stop_loss"]:
                                await self._cancel(self.sell_order_id)
                                self.sell_order_id = None
                                sq = round(self.qty * p["sell_ratio"] / 100, 4)
                                ms = await self._sell(sq)
                                if ms:
                                    pnl = sq * (price - self.avg_price)
                                    self._record("sell", "hard_stop", sq, price, pnl, ms)
                                    self.holding = p["sell_ratio"] < 100
                                self.sell_target = self.sell_avg = self.sell_qty_pending = 0.0
                                self.waiting = None
                        await asyncio.sleep(p["interval"])
                        continue
                    # 挂单进入终态但未成交（被撤）→ 清状态，下一轮重挂
                    self.sell_order_id = None
                    self.sell_target = self.sell_avg = self.sell_qty_pending = 0.0
                    self.waiting = None
                    await asyncio.sleep(p["interval"])
                    continue

                self.waiting = None

                # 每日熔断（仅限制新开买入）
                if self.trades_today >= p["max_trades_day"]:
                    await asyncio.sleep(p["interval"])
                    continue

                # ===== 状态③：持仓但还没挂止盈 → 按 profit_pct 在均价之上挂 sell-limit =====
                if self.holding and self.qty > 0 and self.avg_price > 0:
                    gain_pct = (price - self.avg_price) / self.avg_price * 100 if price > 0 else 0.0
                    sell_qty = round(self.qty * p["sell_ratio"] / 100, 4)

                    # 已深亏 → 直接市价硬止损，不挂止盈
                    if price > 0 and gain_pct <= -p["stop_loss"]:
                        ms = await self._sell(sell_qty)
                        if ms:
                            pnl = sell_qty * (price - self.avg_price)
                            self._record("sell", "hard_stop", sell_qty, price, pnl, ms)
                            self.holding = p["sell_ratio"] < 100
                        await asyncio.sleep(3)
                        continue

                    # 指标止损（亏损 + RSI 超卖 + MACD 下行）→ 市价
                    if ind and gain_pct < 0 and ind["rsi"] < SIGNAL_STOP_RSI \
                            and ind["macd_diff"] < 0:
                        ms = await self._sell(sell_qty)
                        if ms:
                            pnl = sell_qty * (price - self.avg_price)
                            self._record("sell", "signal_stop", sell_qty, price, pnl, ms)
                            self.holding = p["sell_ratio"] < 100
                        await asyncio.sleep(3)
                        continue

                    # 重启对账：T212 已存在本标的 sell-limit 则接管，避免重复挂单
                    existing = await self._open_sell_limit()
                    if existing and existing.get("id"):
                        self.sell_order_id = existing["id"]
                        self.sell_target = float(existing.get("limitPrice") or 0)
                        self.sell_qty_pending = abs(float(existing.get("quantity") or sell_qty))
                        self.sell_avg = self.avg_price
                        log.info("quant %s 接管已有止盈挂单 %s @ %.4f",
                                 self.symbol, self.sell_order_id, self.sell_target)
                        await asyncio.sleep(p["interval"])
                        continue

                    # 挂止盈 sell-limit：限价 = 均价 ×(1+profit_pct%)，保证成交价高于均价
                    target = round(self.avg_price * (1 + p["profit_pct"] / 100), 2)
                    order = await self._sell_limit(sell_qty, target)
                    if order and order.get("id"):
                        self.sell_order_id = order["id"]
                        self.sell_target = target
                        self.sell_avg = self.avg_price
                        self.sell_qty_pending = sell_qty
                        self._record("sell_limit", "set_profit_limit", sell_qty,
                                     target, None, order, count=False)
                        log.info("quant %s 已挂止盈 %s @ %.4f (均价 %.4f +%.2f%%)",
                                 self.symbol, self.sell_order_id, target,
                                 self.avg_price, p["profit_pct"])
                    await asyncio.sleep(p["interval"])
                    continue

                # ===== 状态④：空仓 → 评估买入 =====
                if not self.holding and price > 0:
                    buy_mode = p.get("buy_mode", "ind")
                    should_buy = False
                    buy_reason = "ind_buy"

                    if buy_mode == "market":
                        should_buy = True
                        buy_reason = "market_buy"
                    elif buy_mode == "ind" and ind:
                        if ind["rsi"] < p["rsi_buy"] and ind["macd_diff"] > 0:
                            should_buy = True

                    if should_buy:
                        cash = await self._cash()
                        value = cash * p["budget_ratio"] / 100
                        if p.get("budget_eur", 0) > 0:
                            value = min(value, p["budget_eur"])
                        if value >= 1:
                            order = await self._buy(value)
                            if order and order.get("id"):
                                est_qty = round(value / price, 4)
                                self._record("buy", buy_reason, est_qty, price, None, order)
                                # 记录买入挂单 id，下一轮确认成交后再挂止盈
                                self.buy_order_id = order["id"]
                                self.waiting = "buy"
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
            "env": settings.T212_ENV,
            "params": self.params,
            "holding": self.holding, "quantity": self.qty,
            "avg_price": self.avg_price, "last_price": self.last_price,
            "gain_pct": gain_pct,
            "indicators": self.last_ind,
            "trades_today": self.trades_today,
            "total_trades": self.total_trades,
            "total_pnl": round(self.total_pnl, 2),
            "last_check": self.last_check,
            "last_action": self.last_action,
            "error": self.error,
            # 挂单状态机
            "waiting": self.waiting,                 # None|'buy'|'sell_limit'
            "sell_order_id": self.sell_order_id,
            "sell_target": round(self.sell_target, 4) if self.sell_target else None,
            "sell_qty_pending": self.sell_qty_pending or None,
        }


# ── 引擎管理 ──────────────────────────────────────────────────────────────────

_runners: dict[str, StrategyRunner] = {}


def get_runner(symbol: str) -> StrategyRunner | None:
    return _runners.get(symbol.upper())


def list_runners() -> list[StrategyRunner]:
    return list(_runners.values())


def start(symbol: str, t212_ticker: str, params: dict) -> StrategyRunner:
    sym = symbol.upper()
    old = _runners.get(sym)
    if old and old.task and not old.task.done():
        old.task.cancel()
    runner = StrategyRunner(sym, t212_ticker, params)
    runner.task = asyncio.create_task(runner.run())
    _runners[sym] = runner
    with get_session() as s:
        row = s.get(QuantStrategy, sym)
        if row:
            row.t212_ticker = t212_ticker
            row.params = runner.params
            row.active = True
        else:
            s.add(QuantStrategy(symbol=sym, t212_ticker=t212_ticker,
                                params=runner.params, active=True))
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
    """启动时恢复 active=True 的策略"""
    with get_session() as s:
        rows = s.query(QuantStrategy).filter(
            QuantStrategy.active.is_(True)).all()
        configs = [(r.symbol, r.t212_ticker, dict(r.params or {}))
                   for r in rows]
    for sym, ticker, params in configs:
        start(sym, ticker, params)
        log.info("quant resumed: %s", sym)
    return len(configs)


def shutdown():
    for r in _runners.values():
        if r.task and not r.task.done():
            r.task.cancel()
