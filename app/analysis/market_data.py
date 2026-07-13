"""确定性市场数据层（Equity Research Agent）
=============================================
硬规则：**数字来自数据 API，绝不由 LLM 生成**。本模块把某标的的报价 / 基本面 /
分析师目标 / 财报日 / 技术指标 组装成一个 `market_data` 上下文，供研究 Agent 注入推理，
并在代码里做 skill 标注的 [enforce in code] 护栏：新鲜度校验、信源冲突→区间、合规后处理。
数据来自 yfinance `.info`(复用 research.py 已用字段) + 本地 prices/indicators 库。
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from analysis.compliance import (DISCLAIMER, enforce_compliance,  # re-export 护栏
                                has_advice_language)
from db import get_session
from models import IndicatorDaily, Price

log = logging.getLogger(__name__)

__all__ = ["build_market_data", "market_data_text", "quick_fact_answer",
           "enforce_compliance", "has_advice_language", "DISCLAIMER"]

_INFO_CACHE: dict[str, tuple[float, dict]] = {}   # symbol -> (monotonic_ts, info)
_INFO_TTL = 3600.0                                 # yfinance .info 慢且限流 → 缓存 1h
FRESHNESS_DIVERGENCE_PCT = 8.0                     # 目标价与现价背离超此值即提示
_PRICE_CONFLICT_PCT = 2.0                          # 多源报价分歧超此值→给区间


def _yf_info(symbol: str) -> dict:
    now = time.monotonic()
    hit = _INFO_CACHE.get(symbol)
    if hit and now - hit[0] < _INFO_TTL:
        return hit[1]
    info = {}
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        log.warning("market_data yfinance %s 失败: %s", symbol, e)
    _INFO_CACHE[symbol] = (now, info)
    return info


def _latest_close(symbol: str) -> float | None:
    try:
        with get_session() as db:
            row = db.execute(
                select(Price.close).where(Price.symbol == symbol, Price.interval == "1d")
                .order_by(Price.ts.desc()).limit(1)).scalar()
        return float(row) if row else None
    except Exception:
        return None


def _latest_indicators(symbol: str) -> dict | None:
    try:
        with get_session() as db:
            r = db.execute(
                select(IndicatorDaily).where(IndicatorDaily.symbol == symbol)
                .order_by(IndicatorDaily.ts.desc()).limit(1)).scalar_one_or_none()
        if r:
            return {"date": str(r.ts), "rsi": r.rsi, "macd_cross": r.macd_cross,
                    "sma20": r.sma20, "sma50": r.sma50, "sma200": r.sma200,
                    "atr": r.atr, "vol_ratio": r.vol_ratio,
                    "bb_upper": r.bb_upper, "bb_lower": r.bb_lower}
    except Exception:
        pass
    return None


def _trend(symbol: str) -> dict | None:
    try:
        with get_session() as db:
            rows = db.execute(
                select(Price.ts, Price.high, Price.low, Price.close, Price.volume)
                .where(Price.symbol == symbol, Price.interval == "1d")
                .order_by(Price.ts.desc()).limit(90)).all()
        if len(rows) < 30:
            return None
        rows = list(reversed(rows))
        from analysis.trend import trend_analysis
        tr = trend_analysis([r[0].strftime("%Y-%m-%d") for r in rows],
                            [r[3] for r in rows], [r[4] for r in rows],
                            [r[1] for r in rows], [r[2] for r in rows])
        if tr:
            return {"trend_label": tr.get("trend_label"), "regime": (tr.get("regime") or [None])[-1],
                    "money_flow_usd": tr.get("money_flow_usd"), "relative_volume": tr.get("relative_volume")}
    except Exception as e:
        log.warning("market_data trend %s: %s", symbol, e)
    return None


def _earnings_meta(info: dict, symbol: str) -> dict:
    """下次财报的结构化信息，**永不把过去的日期当作“下次”**。

    yfinance `.info.earningsTimestamp` 常是“最近一次已发布”的财报(过去)，直接取用会在
    盘后/新季度把旧日期误报为“下次财报”。这里汇总所有候选来源(info 时间戳 + calendar 窗口)，
    按“今天”切分未来/过去：有未来日期取最近的一个(confirmed)；全在过去则说明数据源滞后，
    以上次财报按季度外推一个 estimated 日期并明确标注，绝不冒充确认日。

    返回 {date, status: confirmed|estimated|unknown, last_reported, note}。
    """
    today = datetime.now(timezone.utc).date()
    future: list[date] = []
    past: list[date] = []

    def _bucket(d: date | None):
        if d is None:
            return
        (future if d >= today else past).append(d)

    # 1) info 里的时间戳（Start/End 是下次窗口，earningsTimestamp 多为上次已发布）
    for key in ("earningsTimestampStart", "earningsTimestampEnd", "earningsTimestamp"):
        ts = info.get(key)
        if ts:
            try:
                _bucket(datetime.fromtimestamp(int(ts), tz=timezone.utc).date())
            except Exception:
                pass
    # 2) calendar：yfinance 的“下次财报”估计窗口（通常最贴近实际）
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        eds = (cal or {}).get("Earnings Date") if isinstance(cal, dict) else None
        for ed in (eds if isinstance(eds, (list, tuple)) else [eds]):
            if ed is None:
                continue
            try:
                d = ed if isinstance(ed, date) else datetime.fromisoformat(str(ed)[:10]).date()
                _bucket(d)
            except Exception:
                pass
    except Exception:
        pass

    last_reported = max(past).isoformat() if past else None
    if future:
        return {"date": min(future).isoformat(), "status": "confirmed",
                "last_reported": last_reported, "note": ""}
    if past:                      # 数据源只有过去日期 → 滞后，按季度(~91天)外推并标注
        est = max(past)
        while est < today:
            est += timedelta(days=91)
        return {"date": est.isoformat(), "status": "estimated",
                "last_reported": last_reported,
                "note": f"数据源未提供确认的下次财报日；基于上次财报 {last_reported} 按季度外推，"
                        f"为预计值，需以公司官方公告为准"}
    return {"date": None, "status": "unknown", "last_reported": None,
            "note": "数据源未提供财报日期"}


def build_market_data(symbol: str) -> dict:
    """组装确定性 market_data 上下文。所有数字来自数据源，缺失即 None(绝不臆造)。"""
    sym = symbol.split("_")[0].upper()
    info = _yf_info(sym)

    px_yf = info.get("currentPrice") or info.get("regularMarketPrice")
    px_db = _latest_close(sym)
    live = px_yf or px_db
    conflict = None
    if px_yf and px_db and live:
        diff = abs(px_yf - px_db) / live * 100
        if diff > _PRICE_CONFLICT_PCT:
            conflict = {"range": [round(min(px_yf, px_db), 2), round(max(px_yf, px_db), 2)],
                        "note": f"多源报价分歧 {diff:.1f}%（yfinance {px_yf} vs 库内收盘 {px_db}），取区间"}

    fundamentals = {k: info.get(src) for k, src in {
        "market_cap": "marketCap", "forward_pe": "forwardPE", "trailing_pe": "trailingPE",
        "price_to_sales": "priceToSalesTrailing12Months", "ev_ebitda": "enterpriseToEbitda",
        "gross_margin": "grossMargins", "operating_margin": "operatingMargins",
        "profit_margin": "profitMargins", "revenue_growth": "revenueGrowth",
        "earnings_growth": "earningsGrowth", "revenue_ttm": "totalRevenue",
        "free_cashflow": "freeCashflow", "roe": "returnOnEquity", "beta": "beta",
    }.items()}
    fundamentals["basis"] = "yfinance .info（多为 TTM/最新季，含 GAAP 与 Non-GAAP 混合，按标注解读）"

    tmean = info.get("targetMeanPrice")
    analyst = {"target_mean": tmean, "target_high": info.get("targetHighPrice"),
               "target_low": info.get("targetLowPrice"),
               "recommendation_mean": info.get("recommendationMean"),
               "num_analysts": info.get("numberOfAnalystOpinions")}

    freshness = None
    if live and tmean:
        div = (tmean - live) / live * 100
        flag = (tmean < live) or abs(div) >= FRESHNESS_DIVERGENCE_PCT
        if tmean < live:
            note = "分析师均价目标已低于现价 —— 共识很可能滞后于近期走势，勿把旧均价当现值"
        elif flag:
            note = "目标价与现价背离较大，注意共识时效（区分近 2 周内的新目标与旧目标）"
        else:
            note = "目标价与现价基本一致"
        freshness = {"live_price": round(live, 2), "blended_target": round(tmean, 2),
                     "divergence_pct": round(div, 1), "flag": flag, "note": note}

    return {
        "symbol": sym,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "company": {"name": info.get("longName"), "sector": info.get("sector"),
                    "industry": info.get("industry")},
        "quote": {"live_price": round(live, 2) if live else None,
                  "sources": {"yfinance": px_yf, "db_close": px_db}, "conflict": conflict,
                  "change_pct": info.get("regularMarketChangePercent"),
                  "high52": info.get("fiftyTwoWeekHigh"), "low52": info.get("fiftyTwoWeekLow")},
        "fundamentals": fundamentals,
        "analyst": analyst,
        "freshness": freshness,
        "earnings_date": (_em := _earnings_meta(info, sym))["date"],  # 向后兼容(字符串或 None)
        "earnings": _em,        # 结构化: status(confirmed/estimated/unknown)/last_reported/note
        "indicators": _latest_indicators(sym),
        "trend": _trend(sym),
    }


# ── 渲染 / 护栏 ───────────────────────────────────────────────────────────────
def market_data_text(md: dict) -> str:
    """把 market_data 压成注入 LLM 的紧凑上下文块（供推理，不供生成数字）。"""
    import json
    return "【market_data（唯一数字来源，勿臆造）】\n" + json.dumps(md, ensure_ascii=False, default=str)


def quick_fact_answer(md: dict, query: str) -> str | None:
    """快问快答 fast path：直接从 market_data 取数回答，不进 LLM。命中返回文本，否则 None。"""
    q = query
    sym = md["symbol"]
    if any(k in q for k in ("财报", "earnings", "业绩")) and any(k in q for k in ("几号", "日期", "什么时候", "when", "date")):
        em = md.get("earnings") or {}
        d = md.get("earnings_date")
        if not d:
            return f"{sym} 暂无可用的下次财报日期数据。"
        if em.get("status") == "estimated":
            return (f"{sym} 下次财报日预计约 {d}（{em.get('note')}；"
                    f"上次财报 {em.get('last_reported')}）。")
        return f"{sym} 下次财报日：{d}。"
    if any(k in q for k in ("现价", "多少钱", "股价", "价格", "current price", "price")):
        lp = md["quote"].get("live_price")
        return f"{sym} 现价约 {lp}（{md['as_of']}）。" if lp else f"{sym} 暂无实时报价数据。"
    if any(k in q.lower() for k in ("pe", "市盈率", "估值倍数")):
        f = md["fundamentals"]
        return f"{sym} 市盈率：预期 PE {f.get('forward_pe')}、历史 PE {f.get('trailing_pe')}（yfinance）。"
    if any(k in q for k in ("目标价", "target")):
        a = md["analyst"]
        return f"{sym} 分析师目标：均价 {a.get('target_mean')}（区间 {a.get('target_low')}~{a.get('target_high')}，{a.get('num_analysts')} 位分析师）。"
    return None
