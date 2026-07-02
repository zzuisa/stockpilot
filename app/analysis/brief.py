"""美股盘前日报 (Daily Brief) 组装：价格 + 趋势(指标) + 资金/量能 + 期权 + LLM 点评。

输出四标签结构：今日结论 / 趋势证据 / 期权结论 / 观察条件。
复用：indicators(IndicatorDaily)、options.option_metrics、sentiment.symbol_aggregates、
research._llm_client；缺日线指标时由 indicators.ensure_symbol 自愈补全。
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

import settings
from models import IndicatorDaily, Price

log = logging.getLogger(__name__)


def _money_flow(db, symbol: str, period: int = 20) -> dict:
    """近 period 日 Chaikin Money Flow → 主力资金方向 + 相对量能(最新 vol_ratio)。"""
    rows = db.execute(
        select(Price.high, Price.low, Price.close, Price.volume)
        .where(Price.symbol == symbol, Price.interval == "1d")
        .order_by(Price.ts.desc()).limit(period)
    ).all()
    mfv = vol = 0.0
    for h, l, c, v in rows:
        if not (h and l and c and v) or h <= l:
            continue
        mult = ((c - l) - (h - c)) / (h - l)
        mfv += mult * v
        vol += v
    cmf = (mfv / vol) if vol else 0.0
    if cmf > 0.05:
        flow = "流入"
    elif cmf < -0.05:
        flow = "流出"
    else:
        flow = "中性"
    return {"cmf": round(cmf, 3), "flow": flow}


def _chips(ind: dict, mf: dict) -> dict:
    """三/多状态芯片(确定性派生，不靠 LLM)：格局/动量/信号/主力资金/相对量能。"""
    close = ind.get("close") or 0
    s20, s50, s200 = ind.get("sma20"), ind.get("sma50"), ind.get("sma200")
    rsi = ind.get("rsi") or 50
    hist = ind.get("macd_hist") or 0
    cross = ind.get("macd_cross") or 0
    vr = ind.get("vol_ratio")

    # 格局：均线多空排列
    if all(isinstance(x, (int, float)) for x in (s20, s50, s200)) and close:
        if close > s20 and s20 > s50 > s200:
            pattern = "多头格局"
        elif close < s20 and s20 < s50 < s200:
            pattern = "空头格局"
        else:
            pattern = "多空交织"
    else:
        pattern = "数据不足"

    # 动量：MACD 柱方向 + 交叉
    if hist > 0:
        momentum = "向上增强" if cross == 1 else "向上"
    elif hist < 0:
        momentum = "向下衰减"
    else:
        momentum = "动能转弱"

    # 信号：RSI + 动量综合
    bull = (rsi < 70 and hist > 0) + (cross == 1) + (close > (s50 or close))
    bear = (rsi > 70) + (hist < 0) + (cross == -1)
    if bull >= 2 and bear == 0:
        signal = "积极进攻"
    elif bear >= 2:
        signal = "防守谨慎"
    else:
        signal = "观望谨慎"

    # 相对量能
    if isinstance(vr, (int, float)):
        vol_label = (f"{vr:.1f}x " + ("放量" if vr >= 1.2 else "缩量" if vr <= 0.8 else "正常"))
    else:
        vol_label = "—"

    return {
        "pattern": pattern, "momentum": momentum, "signal": signal,
        "money_flow": mf["flow"], "vol_label": vol_label,
    }


_BRIEF_SYSTEM = (
    "你是顶级美股盘前策略师，擅长把技术面、资金面、期权 gamma 结构提炼成一份"
    "简洁犀利的盘前日报。只返回 JSON，不含其他内容。"
)

_BRIEF_USER_TMPL = """## 盘前日报数据: {symbol}  现价 {price}  涨跌 {chg}%

格局: {pattern} | 动量: {momentum} | 信号: {signal} | 主力资金: {flow}(净{mf_usd}) | 相对量能: {vol_label}
技术: RSI {rsi} | MACD柱 {hist} | SMA20/50/200 {s20}/{s50}/{s200} | ATR {atr}
新闻情绪(7日): 均分 {sent} ({news_cnt}条)
期权: GEX {gex} | PCR(OI) {pcr_oi} | IV {iv}% | Call Wall {call_wall} | Put Wall {put_wall} | 预期波动 ±{em}%

请生成盘前日报文案，返回 JSON：
{{"core_take":"今日核心结论，一句话40字内，点出方向+期权gamma+资金特征",
"trend_comment":"趋势证据点评，50字内，说明趋势能否成立(价格方向+资金+量能)",
"options_comment":"期权结论点评，50字内，结合GEX/PCR/IV/Wall说明市场防守或进攻、波动预期",
"levels_comment":"观察条件点评，40字内，说明上方压力下方支撑及区间博弈含义"}}"""


def _llm_comment(facts: dict) -> dict:
    if not settings.llm_enabled:
        return {}
    from analysis.research import _llm_client
    try:
        prompt = _BRIEF_USER_TMPL.format(**facts)
        client = _llm_client()
        resp = client.chat.completions.create(
            model=settings.SILICONFLOW_MODEL,
            messages=[{"role": "system", "content": _BRIEF_SYSTEM},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=600, timeout=90,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("brief llm failed %s: %s", facts.get("symbol"), e)
        return {}


def build_daily_brief(symbol: str, yf_symbol: str | None = None,
                      with_comment: bool = False) -> dict:
    """组装单股盘前日报。缺指标自动补全；无期权时期权页降级。
    with_comment=False(默认)跳过慢的 LLM 解读，仅返回计算数据(图表/数值，~5s)；
    LLM 解读由独立 comment 端点异步生成，避免拖慢主请求触发超时。"""
    from db import get_session
    from analysis.indicators import ensure_symbol
    from analysis.options import option_metrics
    from analysis.sentiment import symbol_aggregates
    sym = symbol.upper()

    # 指标自愈
    from api.research import _get_indicators
    with get_session() as db:
        ind, history = _get_indicators(db, sym)
    if not ind:
        ensure_symbol(sym, yf_symbol)
        with get_session() as db:
            ind, history = _get_indicators(db, sym)
    if not ind:
        return {"ok": False, "error": f"{sym} 无日线数据，无法生成日报"}

    with get_session() as db:
        mf = _money_flow(db, sym)
        agg = symbol_aggregates(db, sym, days=7)
        # 趋势图：近 60 日收盘(prices 表，倒序取后翻正)
        # 趋势分析序列：近 180 日 OHLCV(升序)
        ph = db.execute(
            select(Price.ts, Price.close, Price.volume, Price.high, Price.low)
            .where(Price.symbol == sym, Price.interval == "1d")
            .order_by(Price.ts.desc()).limit(180)).all()
        rows = [(str(t)[:10], float(c), float(v or 0),
                 float(h or c), float(lo or c))
                for t, c, v, h, lo in reversed(ph) if c is not None]
    price_history = [{"ts": d, "close": round(c, 2)} for d, c, _, _, _ in rows]
    prev_close = rows[-1][1] if rows else (ind.get("close") or 0)

    # 卡尔曼趋势分析(快慢线/带/regime/价差/周线共振/美元资金/相对量能)
    from analysis.trend import trend_analysis
    trend = trend_analysis(
        [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
        highs=[r[3] for r in rows], lows=[r[4] for r in rows],
    ) if len(rows) >= 30 else None

    opt = option_metrics(sym, yf_symbol)
    price = (opt or {}).get("spot") or ind.get("close") or 0
    chg = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    # 主力资金/量能优先用趋势分析(美元口径)，回退 CMF
    if trend:
        mf["flow"] = ("流入" if trend["money_flow_usd"] > 0 else
                      "流出" if trend["money_flow_usd"] < 0 else "中性")
        ind = {**ind, "vol_ratio": trend["relative_volume"]}
    chips = _chips(ind, mf)
    if trend:
        chips["pattern"] = "偏强格局" if trend["trend_label"] in ("偏强", "中性偏多") else \
            ("偏弱格局" if trend["trend_label"] in ("偏弱", "中性偏空") else chips["pattern"])

    def fv(v, f=".2f"):
        return f"{v:{f}}" if isinstance(v, (int, float)) else "N/A"

    facts = {
        "symbol": sym, "price": fv(price), "chg": chg,
        "pattern": chips["pattern"], "momentum": chips["momentum"],
        "signal": chips["signal"], "flow": chips["money_flow"],
        "vol_label": chips["vol_label"],
        "mf_usd": (f"{trend['money_flow_usd']/1e4:.0f}万美元" if trend else "N/A"),
        "rsi": fv(ind.get("rsi"), ".1f"), "hist": fv(ind.get("macd_hist"), ".4f"),
        "s20": fv(ind.get("sma20")), "s50": fv(ind.get("sma50")),
        "s200": fv(ind.get("sma200")), "atr": fv(ind.get("atr"), ".2f"),
        "sent": fv(agg.get("sent_avg"), ".1f"), "news_cnt": agg.get("news_cnt", 0),
        "gex": fv((opt or {}).get("gex")) if opt else "N/A",
        "pcr_oi": (opt or {}).get("pcr_oi", "N/A") if opt else "N/A",
        "iv": fv((opt or {}).get("iv_atm", 0) * 100, ".0f") if opt and opt.get("iv_atm") else "N/A",
        "call_wall": (opt or {}).get("call_wall", "N/A") if opt else "N/A",
        "put_wall": (opt or {}).get("put_wall", "N/A") if opt else "N/A",
        "em": (opt or {}).get("expected_move_pct", "N/A") if opt else "N/A",
    }
    comment = _llm_comment(facts) if with_comment else {}

    return {
        "ok": True, "symbol": sym,
        "facts": facts,
        "ts": datetime.now(timezone.utc).isoformat(),
        "price": round(price, 2), "prev_close": round(prev_close, 2), "change_pct": chg,
        "chips": chips,
        "indicators": ind, "history": history,
        "price_history": price_history,
        "trend": trend,
        "sentiment": agg,
        "options": opt,
        "comment": comment,
    }
