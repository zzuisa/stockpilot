"""全天候 ETF 回测引擎 (All Weather Lab)
=========================================
复刻「全天候投资 + 月度定投 + 机会仓(回撤分档加仓)」策略，对照不同再平衡周期。

口径说明：
- 数据：prices 表 1d 复权 OHLC(yfinance auto_adjust=总回报)，开盘价撮合、收盘价估值。
- 收益：剔除当日外部现金流后的「时间加权」日收益 → 年化复合(CAGR)、波动、最大回撤、Sharpe。
- 基础仓：每月定投按目标权重买入；每 N 月把基础仓拉回目标权重(机会仓不参与再平衡)。
- 机会仓：每品种独立跟踪前高与回撤，命中档位即按额加仓；同一轮回撤每档只触发一次，
          价格修复前高后解锁下一轮；买入独立持有，不参与再平衡。
"""
import logging

import numpy as np
import pandas as pd
from sqlalchemy import select

from db import get_session
from models import Price

log = logging.getLogger(__name__)

TRADING_DAYS = 252

# yfinance 符号映射（BTC 需 BTC-USD）
YF_MAP = {"BTC": "BTC-USD"}
ALL_WEATHER_SYMBOLS = ["SPY", "IEF", "TLT", "GLD", "DBC", "QQQ", "BTC"]

# 默认目标权重（合计 100；用户可改）
DEFAULT_WEIGHTS = {"SPY": 30, "TLT": 25, "IEF": 15, "GLD": 13, "DBC": 10, "QQQ": 5, "BTC": 2}
# 默认机会仓：SPY / QQQ 各三档回撤加仓
DEFAULT_OPPORTUNITY = [
    {"symbol": "SPY", "tiers": [{"dd": 10, "amount": 5000}, {"dd": 20, "amount": 10000}, {"dd": 30, "amount": 15000}]},
    {"symbol": "QQQ", "tiers": [{"dd": 10, "amount": 5000}, {"dd": 20, "amount": 10000}, {"dd": 30, "amount": 15000}]},
]
# 对照的再平衡周期(月)，0 = 不再平衡
DEFAULT_REBALANCE_SET = [2, 3, 6, 12, 0]
DEFAULT_PRIMARY_REBALANCE = 3
ASSET_LABEL = {"SPY": "标普500", "IEF": "中期美债", "TLT": "长期美债", "GLD": "黄金",
               "DBC": "大宗商品", "QQQ": "纳指100", "BTC": "比特币"}


# ── 数据加载 ──────────────────────────────────────────────────────────────────
def load_prices(symbols, start=None, end=None):
    """读取多标的 1d OHLC，按 SPY 交易日历对齐(缺失前向填充)，区间 [start,end]，
    并裁掉任一标的尚无数据的行 → 得到所有标的都存在的「有效区间」。
    返回 (closes:{sym:np.array}, opens:{sym:np.array}, dates:DatetimeIndex)。"""
    syms = list(dict.fromkeys(symbols))
    frames = {}
    with get_session() as s:
        for sym in syms:
            rows = s.execute(
                select(Price.ts, Price.open, Price.close)
                .where(Price.symbol == sym, Price.interval == "1d")
                .order_by(Price.ts)
            ).all()
            if rows:
                df = pd.DataFrame(rows, columns=["ts", "open", "close"])
                df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.normalize()
                frames[sym] = df.drop_duplicates("ts").set_index("ts")
    if not frames:
        return {}, {}, pd.DatetimeIndex([])

    idx = frames["SPY"].index if "SPY" in frames else \
        pd.DatetimeIndex(sorted(set().union(*[f.index for f in frames.values()])))
    close = pd.DataFrame(index=idx)
    open_ = pd.DataFrame(index=idx)
    for sym, df in frames.items():
        close[sym] = df["close"].reindex(idx).ffill()
        open_[sym] = df["open"].reindex(idx).ffill().fillna(close[sym])

    if start:
        m = close.index >= pd.Timestamp(start, tz="UTC")
        close, open_ = close[m], open_[m]
    if end:
        m = close.index <= pd.Timestamp(end, tz="UTC")
        close, open_ = close[m], open_[m]
    # 仅保留所有标的都有数据的行 → 有效区间
    close = close.dropna(how="any")
    open_ = open_.reindex(close.index)
    closes = {s: close[s].to_numpy(dtype=float) for s in close.columns}
    opens = {s: open_[s].to_numpy(dtype=float) for s in open_.columns}
    return closes, opens, close.index


# ── 指标 ──────────────────────────────────────────────────────────────────────
def _metrics(equity, contrib, dates):
    """时间加权口径：日收益剔除当日外部现金流后计算。"""
    eq = np.asarray(equity, float)
    c = np.asarray(contrib, float)
    n = len(eq)
    ret = np.zeros(n)
    for i in range(1, n):
        prev = eq[i - 1]
        if prev > 0:
            ret[i] = (eq[i] - c[i]) / prev - 1.0
    twr = np.cumprod(1.0 + ret)
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    cagr = twr[-1] ** (1.0 / years) - 1.0 if twr[-1] > 0 else 0.0
    r = ret[1:]
    vol = float(r.std() * np.sqrt(TRADING_DAYS)) if len(r) > 1 else 0.0
    sharpe = float(r.mean() * np.sqrt(TRADING_DAYS) / r.std()) if r.std() > 0 else 0.0
    peak = np.maximum.accumulate(twr)
    dd = twr / peak - 1.0
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "maxdd": float(dd.min()),
            "twr": twr, "dd": dd, "ret": ret}


# ── 单次回测 ──────────────────────────────────────────────────────────────────
def _run(closes, opens, dates, weights, monthly_dca=0.0, initial=0.0,
         rebalance_months=0, opportunity=None, opp_cap=None, exec_open=True):
    """跑一条策略。weights:{sym:权重}; rebalance_months=0 不再平衡。
    返回 dict(含 equity/contrib/per_asset/max_drift/rebalances/opportunities/metrics...)。"""
    syms = [s for s in weights if weights[s] > 0 and s in closes]
    wsum = sum(weights[s] for s in syms) or 1.0
    tw = {s: weights[s] / wsum for s in syms}
    opp_cfg = {o["symbol"]: o for o in (opportunity or []) if o["symbol"] in closes}
    px = closes
    ex = opens if exec_open else closes
    n = len(dates)

    base_sh = {s: 0.0 for s in syms}
    opp_sh = {}
    net_inv = {s: 0.0 for s in syms}     # 基础仓每资产净投入($)
    opp_inv = {}                         # 机会仓每资产投入($)
    dca_total = 0.0
    opp_total = 0.0
    max_dev = {s: 0.0 for s in syms}     # 权重对目标的最大偏离(带符号)
    opp_peak = {s: None for s in opp_cfg}
    opp_fired = {s: set() for s in opp_cfg}

    equity = np.zeros(n)
    contrib = np.zeros(n)
    rebalances, opportunities = [], []
    months_since_reb = 0
    prev_month = None

    def buy(sh, inv, sym, dollars, price):
        if price > 0 and dollars > 0:
            sh[sym] = sh.get(sym, 0.0) + dollars / price
            inv[sym] = inv.get(sym, 0.0) + dollars

    for i in range(n):
        ts = dates[i]
        ym = (ts.year, ts.month)
        new_month = prev_month is None or ym != prev_month

        if i == 0 and initial > 0:
            contrib[i] += initial
            for s in syms:
                buy(base_sh, net_inv, s, initial * tw[s], ex[s][i])

        if new_month and monthly_dca > 0:
            contrib[i] += monthly_dca
            dca_total += monthly_dca
            for s in syms:
                buy(base_sh, net_inv, s, monthly_dca * tw[s], ex[s][i])
            if prev_month is not None:
                months_since_reb += 1
            prev_month = ym
            # 再平衡(每 N 月，定投后执行)
            if rebalance_months and months_since_reb >= rebalance_months:
                months_since_reb = 0
                base_val = sum(base_sh[s] * px[s][i] for s in syms)
                if base_val > 0:
                    trades = []
                    for s in syms:
                        diff = base_val * tw[s] - base_sh[s] * px[s][i]
                        if ex[s][i] > 0 and abs(diff) > 1.0:
                            base_sh[s] += diff / ex[s][i]
                            net_inv[s] += diff   # 卖出为负 → 净投入可为负
                            trades.append({"symbol": s, "delta_usd": round(diff, 2)})
                    if trades:
                        rebalances.append({"date": str(ts.date()), "trades": trades})

        # 机会仓：回撤分档加仓
        for s, cfg in opp_cfg.items():
            p = px[s][i]
            pk = opp_peak[s]
            if pk is None:
                opp_peak[s] = p
            elif p >= pk:
                opp_peak[s] = p
                opp_fired[s] = set()          # 修复前高 → 解锁
            else:
                dd = (p / pk - 1.0) * 100.0
                for ti, tier in enumerate(cfg["tiers"]):
                    if ti in opp_fired[s]:
                        continue
                    if dd <= -abs(tier["dd"]):
                        amt = float(tier["amount"])
                        if opp_cap and opp_total + amt > opp_cap:
                            continue
                        buy(opp_sh, opp_inv, s, amt, ex[s][i])
                        opp_total += amt
                        contrib[i] += amt
                        opp_fired[s].add(ti)
                        opportunities.append({"date": str(ts.date()), "symbol": s,
                                              "tier": ti + 1, "drawdown_pct": round(dd, 1),
                                              "amount": amt})

        # 权重漂移(基础仓)
        base_val = sum(base_sh[s] * px[s][i] for s in syms)
        if base_val > 0:
            for s in syms:
                dev = base_sh[s] * px[s][i] / base_val - tw[s]
                if abs(dev) > abs(max_dev[s]):
                    max_dev[s] = dev

        # 估值
        opp_val = sum(q * px[s][i] for s, q in opp_sh.items())
        equity[i] = base_val + opp_val

    m = _metrics(equity, contrib, dates)
    last = n - 1
    per_asset = []
    for s in syms:
        fv = base_sh[s] * px[s][last] + opp_sh.get(s, 0.0) * px[s][last]
        ni = net_inv[s] + opp_inv.get(s, 0.0)
        per_asset.append({"symbol": s, "label": ASSET_LABEL.get(s, s),
                          "target_weight": round(tw[s] * 100, 1),
                          "net_invested": round(ni, 0), "final_value": round(fv, 0),
                          "profit": round(fv - ni, 0),
                          "max_drift": round(max_dev[s] * 100, 1)})
    return {
        "equity": equity, "contrib": contrib, "dates": dates,
        "final": float(equity[last]), "dca_total": round(dca_total, 0),
        "opp_total": round(opp_total, 0),
        "total_invested": round(dca_total + opp_total + initial, 0),
        "net_profit": round(float(equity[last]) - (dca_total + opp_total + initial), 0),
        "metrics": m, "per_asset": per_asset,
        "max_drift_overall": round(max((abs(v) for v in max_dev.values()), default=0) * 100, 1),
        "rebalances": rebalances, "opportunities": opportunities,
    }


def _annual_returns(twr, dates):
    """按自然年的时间加权收益。"""
    s = pd.Series(twr, index=pd.DatetimeIndex(dates))
    out = []
    for yr, grp in s.groupby(s.index.year):
        out.append({"year": int(yr), "return_pct": round((grp.iloc[-1] / grp.iloc[0] - 1) * 100, 2)})
    return out


def _row(name, kind, r):
    m = r["metrics"]
    return {"name": name, "type": kind,
            "cagr": round(m["cagr"] * 100, 2), "vol": round(m["vol"] * 100, 2),
            "maxdd": round(m["maxdd"] * 100, 2), "sharpe": round(m["sharpe"], 4),
            "final": r["final"], "total_invested": r["total_invested"],
            "net_profit": r["net_profit"], "dca_total": r["dca_total"],
            "opp_total": r["opp_total"]}


# ── 编排 ──────────────────────────────────────────────────────────────────────
def run_backtest(cfg: dict) -> dict:
    weights = cfg.get("weights") or DEFAULT_WEIGHTS
    opportunity = cfg.get("opportunity", DEFAULT_OPPORTUNITY)
    monthly_dca = float(cfg.get("monthly_dca", 1400))
    initial = float(cfg.get("initial", 0))
    primary = int(cfg.get("rebalance_months", DEFAULT_PRIMARY_REBALANCE))
    reb_set = cfg.get("rebalance_set", DEFAULT_REBALANCE_SET)
    opp_cap = cfg.get("opp_cap")
    benchmark = cfg.get("benchmark", "SPY")

    base_syms = [s for s in weights if weights[s] > 0]
    singles = list(dict.fromkeys(base_syms + [benchmark]))
    need = list(dict.fromkeys(base_syms + [o["symbol"] for o in opportunity] + [benchmark]))
    closes, opens, dates = load_prices(need, cfg.get("start"), cfg.get("end"))
    if not closes or len(dates) < 30:
        return {"error": "数据不足：请先在「更新数据」拉取历史行情(需 SPY 等标的日线)。",
                "available": sorted(closes.keys())}

    # 主结果(选定再平衡周期)
    primary_run = _run(closes, opens, dates, weights, monthly_dca, initial,
                       primary, opportunity, opp_cap)
    pm = primary_run["metrics"]

    # 多策略对比：各再平衡周期 + SPY 基准 + 各单资产定投
    comparison = []
    for k in reb_set:
        r = _run(closes, opens, dates, weights, monthly_dca, initial, int(k), opportunity, opp_cap)
        label = "不再平衡" if int(k) == 0 else f"每 {int(k)} 个月再平衡"
        comparison.append(_row(label, "组合策略", r))
    for s in singles:
        r = _run(closes, opens, dates, {s: 100}, monthly_dca, initial, 0, [], None)
        comparison.append(_row(f"{s} 单资产定投", "单资产基准", r))

    # 交互式账户状态序列
    eq = primary_run["equity"]
    twr = pm["twr"]
    dd = pm["dd"]
    cum = (twr - 1) * 100
    ann = np.where(np.arange(len(twr)) >= 20,
                   np.power(np.clip(twr, 1e-9, None),
                            TRADING_DAYS / np.maximum(np.arange(len(twr)), 1)) - 1, np.nan) * 100
    series = {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "equity": [round(float(x), 2) for x in eq],
        "drawdown": [round(float(x) * 100, 2) for x in dd],
        "cumulative": [round(float(x), 2) for x in cum],
        "annualized": [None if np.isnan(x) else round(float(x), 2) for x in ann],
    }

    return {
        "effective_range": [dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")],
        "primary_rebalance": primary,
        "kpi": {
            "cagr": round(pm["cagr"] * 100, 2), "vol": round(pm["vol"] * 100, 2),
            "maxdd": round(pm["maxdd"] * 100, 2), "sharpe": round(pm["sharpe"], 2),
            "final": primary_run["final"], "dca_total": primary_run["dca_total"],
            "opp_total": primary_run["opp_total"], "total_invested": primary_run["total_invested"],
            "net_profit": primary_run["net_profit"],
        },
        "comparison": comparison,
        "per_asset": primary_run["per_asset"],
        "max_drift_overall": primary_run["max_drift_overall"],
        "annual_returns": _annual_returns(twr, dates),
        "opportunities": primary_run["opportunities"],
        "rebalances": primary_run["rebalances"],
        "series": series,
        "config": {"weights": weights, "monthly_dca": monthly_dca, "initial": initial,
                   "opportunity": opportunity},
    }
