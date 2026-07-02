"""前瞻短线投资建议（融合盘前日报多维信息 + 窗口线索），SSE 流式。

用户风格：短线(1-14天)、是否处于短期低点、何时卖出。要求每条判断给理论支撑
(技术 RSI/MACD/布林、资金 CMF/相对量能、期权 GEX/Call-Put Wall/预期波动、
Kalman 趋势 regime、新闻/社区情绪)，非一句话总结。

复用：analysis.brief.build_daily_brief(多维数据)、price_attribution_agents 的
_gather_clues/_price_stats(窗口线索)、_llm/_run_llm/_emit/_parse_json(流式 LLM)。
右侧常备面板(无 range=最近窗口)与归因面板(range 终点≈实时)共用本模块。
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# 距今 ≤ ~1.5 天视为"含至当前实时"，才给前瞻建议；否则仅复盘。
_REALTIME_SLACK = timedelta(days=1, hours=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None):
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def build_advice_context(symbol: str, start: str | None = None,
                         end: str | None = None) -> dict:
    """融合盘前日报多维数据 + 窗口线索。range 缺省=最近 ~10 天窗口。
    返回 {facts, chips, trend, options, sentiment, price, change_pct,
          window:{start,end}, is_realtime, price_stats, news, brief, indicators}。"""
    from analysis.brief import build_daily_brief
    import price_attribution_agents as paa

    sym = symbol.upper()
    end_dt = _parse_dt(end) or _now()
    start_dt = _parse_dt(start) or (end_dt - timedelta(days=10))
    is_realtime = (_now() - end_dt) <= _REALTIME_SLACK

    yf_sym = None
    try:
        from db import get_session
        import config
        with get_session() as s:
            for d in config.active_symbols(s):
                if d["symbol"] == sym:
                    yf_sym = d.get("yf_symbol")
                    break
    except Exception:
        pass

    brief = build_daily_brief(sym, yf_sym, with_comment=False)
    ps, clues = paa._gather_clues(sym, start_dt, end_dt)

    return {
        "symbol": sym,
        "ok": bool(brief.get("ok")),
        "facts": brief.get("facts") if brief.get("ok") else None,
        "chips": brief.get("chips") if brief.get("ok") else None,
        "trend": brief.get("trend") if brief.get("ok") else None,
        "options": brief.get("options") if brief.get("ok") else None,
        "sentiment": brief.get("sentiment") if brief.get("ok") else None,
        "price": brief.get("price"),
        "change_pct": brief.get("change_pct"),
        "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "is_realtime": is_realtime,
        "price_stats": ps,
        "news": clues.get("news"),
        "brief": clues.get("brief"),
        "indicators": clues.get("indicators"),
        "community": clues.get("community"),
    }


_SYSTEM_ADVICE = (
    "你是资深短线策略师，服务的交易者风格是**短线(1-14天)波段**，核心关切：①当前是否处于"
    "短期低点(适合进场/加仓)②何时卖出(止盈目标位、减仓与止损条件)。请综合【系统已算好的】"
    "技术面(RSI/MACD柱与金叉死叉/布林/SMA/ATR)、资金面(CMF主力资金流/相对量能)、期权结构"
    "(GEX/Call Wall/Put Wall/预期波动 IV)、Kalman 趋势 regime、新闻与社区情绪，给出**有理论支撑**"
    "的判断——每条结论都要指出依据的指标/理论，禁止空泛的一句话。中文，客观克制，不要免责声明堆砌。"
)

_SYSTEM_JSON = (
    "把你的分析压缩成 JSON，只输出 JSON：\n"
    '{"stance":"偏多|偏空|中性","is_near_low":true/false,'
    '"entry":"进场/加仓时机与价位区间(结合支撑/Put Wall/超卖)",'
    '"exit":"止盈目标位、减仓与止损条件(结合压力/Call Wall/ATR)",'
    '"horizon":"短线1-14天的方向判断一句话",'
    '"thesis":[{"dim":"技术|资金|期权|情绪|趋势","point":"结论","support":"依据的指标/理论"}],'
    '"confidence":0-100,"caveats":"一句话风险提示"}'
)


def _facts_human(ctx: dict) -> str:
    parts = [f"标的 {ctx['symbol']}，现价 {ctx.get('price')}，日内 {ctx.get('change_pct')}%。"]
    if not ctx.get("is_realtime"):
        parts.append("【注意】所选区间未含最新实时数据，请以复盘为主、前瞻建议需谨慎标注不确定。")
    if ctx.get("facts"):
        parts.append("盘前多维数据(技术/资金/期权/情绪):\n"
                     + json.dumps(ctx["facts"], ensure_ascii=False))
    if ctx.get("chips"):
        parts.append("派生格局: " + json.dumps(ctx["chips"], ensure_ascii=False))
    if ctx.get("trend"):
        parts.append("Kalman趋势/资金: " + json.dumps(ctx["trend"], ensure_ascii=False))
    if ctx.get("price_stats"):
        parts.append("窗口价格统计: " + json.dumps(ctx["price_stats"], ensure_ascii=False))
    if ctx.get("news"):
        parts.append("窗口新闻(已情绪分析): " + json.dumps(ctx["news"][:8], ensure_ascii=False))
    if ctx.get("brief"):
        parts.append("新闻精华: " + json.dumps(ctx["brief"], ensure_ascii=False))
    return "\n".join(parts)


async def stream_advice(symbol: str, emit, start: str | None = None,
                        end: str | None = None) -> dict:
    """SSE：先流式输出推理过程(agent='advice')，再产出结构化 JSON 建议(type='result')。"""
    import price_attribution_agents as paa

    await paa._emit(emit, {"type": "phase", "agent": "context", "status": "running",
                           "detail": "融合技术/资金/期权/情绪/趋势数据…"})
    ctx = await asyncio.to_thread(build_advice_context, symbol, start, end)
    if not ctx.get("ok"):
        await paa._emit(emit, {"type": "error",
                               "message": f"{symbol} 缺少日线/指标数据，暂无法生成建议"})
        return {}
    await paa._emit(emit, {"type": "phase", "agent": "context", "status": "done",
                           "detail": ("含实时数据 → 前瞻建议" if ctx["is_realtime"]
                                      else "历史区间 → 复盘为主")})

    human = _facts_human(ctx)
    if not settings_llm_enabled():
        data = _fallback_advice(ctx)
        await paa._emit(emit, {"type": "delta", "agent": "advice",
                               "text": "（未配置 LLM，按指标规则给出简版建议。）"})
        await paa._emit(emit, {"type": "result", "data": {**data, **_meta(ctx)}})
        return data

    # 1) 流式推理过程
    reasoning = await paa._run_llm("advice", _SYSTEM_ADVICE, human, emit, temperature=0.35)

    # 2) 结构化 JSON
    from langchain_core.messages import HumanMessage, SystemMessage
    data = None
    try:
        async with paa._LLM_SEM:
            resp = await paa._llm(0.2, streaming=False).ainvoke([
                SystemMessage(content=_SYSTEM_JSON),
                HumanMessage(content=human + "\n\n[你的分析]\n" + reasoning),
            ])
        data = paa._parse_json(resp.content)
    except Exception as e:
        log.warning("advice synth 失败 %s: %s", symbol, e)
    if not data:
        data = _fallback_advice(ctx)
    data.setdefault("caveats", "短线判断随行情快速变化，仅供参考，非投资建议。")
    payload = {**data, **_meta(ctx)}
    await paa._emit(emit, {"type": "result", "data": payload})
    return payload


def _meta(ctx: dict) -> dict:
    return {
        "is_realtime": ctx.get("is_realtime"),
        "price": ctx.get("price"),
        "change_pct": ctx.get("change_pct"),
        "window": ctx.get("window"),
        "created_at": _now().isoformat(),
    }


def _fallback_advice(ctx: dict) -> dict:
    """无 LLM/失败：按已算好的 chips/指标给规则化简版建议。"""
    chips = ctx.get("chips") or {}
    facts = ctx.get("facts") or {}
    signal = chips.get("signal") or ""
    stance = ("偏多" if "进攻" in signal else "偏空" if "防守" in signal else "中性")
    thesis = []
    if chips.get("pattern"):
        thesis.append({"dim": "技术", "point": chips["pattern"],
                       "support": f"均线格局 + 动量 {chips.get('momentum','')}"})
    if chips.get("money_flow"):
        thesis.append({"dim": "资金", "point": f"主力资金{chips['money_flow']}",
                       "support": f"CMF/相对量能 {chips.get('vol_label','')}"})
    return {
        "stance": stance,
        "is_near_low": facts.get("rsi") not in (None, "N/A") and False,
        "entry": "结合下方支撑分批，避免追高（简版，未走 LLM）。",
        "exit": "触及上方压力或跌破关键支撑减仓（简版，未走 LLM）。",
        "horizon": f"综合信号：{signal or '观望'}。",
        "thesis": thesis,
        "confidence": 30,
    }


def settings_llm_enabled() -> bool:
    import settings
    return settings.llm_enabled
