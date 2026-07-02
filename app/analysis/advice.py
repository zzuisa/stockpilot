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
    market = "US"
    futu_kw = sym
    try:
        from db import get_session
        import config
        with get_session() as s:
            for d in config.active_symbols(s):
                if d["symbol"] == sym:
                    yf_sym = d.get("yf_symbol")
                    market = config.market_of(d)
                    futu_kw = config.futu_keyword(d)
                    break
    except Exception:
        pass

    brief = build_daily_brief(sym, yf_sym, with_comment=False)
    ps, clues = paa._gather_clues(sym, start_dt, end_dt)

    return {
        "symbol": sym,
        "market": market,
        "futu_keyword": futu_kw,
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
    fr = ctx.get("futu_research")
    if fr and fr.get("summary"):
        parts.append("Futu 多源情报(富途新闻/评级/资金/快照，由 Qwen 研究员汇总):\n"
                     + str(fr["summary"])[:2000])
    return "\n".join(parts)


# 进程级串行池：一次只跑一个建议生成，避免频繁切换标的时并行重复刷 LLM 浪费 token
_ADVICE_SEM = asyncio.Semaphore(1)


async def stream_advice(symbol: str, emit, start: str | None = None,
                        end: str | None = None) -> dict:
    """SSE：先流式输出推理过程(agent='advice')，再产出结构化 JSON 建议(type='result')。
    全程受 _ADVICE_SEM 串行；产出后落库(advice_results, 每标的保留最新 N 条)。"""
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

    if not settings_llm_enabled():
        data = _fallback_advice(ctx)
        await paa._emit(emit, {"type": "delta", "agent": "advice",
                               "text": "（未配置 LLM，按指标规则给出简版建议。）"})
        payload = {**data, **_meta(ctx)}
        await asyncio.to_thread(_persist_advice, ctx, payload, "", 0)
        await paa._emit(emit, {"type": "result", "data": payload})
        return payload

    # 串行池：若已有建议在跑，这里排队(提示 queued)，一次只放行一个
    if _ADVICE_SEM.locked():
        await paa._emit(emit, {"type": "phase", "agent": "queued", "status": "running",
                               "detail": "已有分析在进行，排队中…"})
    async with _ADVICE_SEM:
        # B5: 非美股 + Futu 可用 → Qwen 工具代理汇总富途多源情报，交 DeepSeek
        try:
            from analysis import futu_skills, research_agent
            if ctx.get("market") != "US" and futu_skills.available():
                await paa._emit(emit, {"type": "phase", "agent": "futu",
                                       "status": "running",
                                       "detail": "Qwen 研究员调用富途多源数据…"})
                ctx["futu_research"] = await research_agent.gather_dossier(
                    ctx["symbol"], ctx.get("market"), ctx.get("futu_keyword"), emit)
        except Exception as e:
            log.warning("futu 研究员 %s 失败(降级): %s", symbol, e)

        human = _facts_human(ctx)
        # 1) 流式推理过程
        reasoning = await paa._run_llm("advice", _SYSTEM_ADVICE, human, emit,
                                       temperature=0.35)
        # 2) 结构化 JSON(DeepSeek)
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
        await asyncio.to_thread(_persist_advice, ctx, payload, reasoning, 0)
    await paa._emit(emit, {"type": "result", "data": payload})
    return payload


# ─── 持久化 + 缓存 + 历史(每标的保留最新 ADVICE_KEEP 条) ───

def _persist_advice(ctx: dict, payload: dict, reasoning: str, tokens: int) -> None:
    import settings
    from db import get_session
    from models import AdviceResult
    from sqlalchemy import select
    w = ctx.get("window") or {}
    try:
        with get_session() as s:
            s.add(AdviceResult(
                symbol=ctx["symbol"], window_start=_parse_dt(w.get("start")),
                window_end=_parse_dt(w.get("end")),
                is_realtime=bool(ctx.get("is_realtime")),
                stance=payload.get("stance"), result=payload,
                reasoning=reasoning or "", tokens=tokens or 0))
            s.flush()
            ids = s.execute(
                select(AdviceResult.id).where(AdviceResult.symbol == ctx["symbol"])
                .order_by(AdviceResult.ts.desc())).scalars().all()
            for old in ids[settings.ADVICE_KEEP:]:
                s.delete(s.get(AdviceResult, old))
    except Exception as e:
        log.warning("advice 落库失败 %s: %s", ctx.get("symbol"), e)


def latest_advice(symbol: str) -> dict | None:
    from db import get_session
    from models import AdviceResult
    from sqlalchemy import select
    with get_session() as s:
        r = s.execute(select(AdviceResult).where(
            AdviceResult.symbol == symbol.upper())
            .order_by(AdviceResult.ts.desc()).limit(1)).scalars().first()
        return _row_dict(r) if r else None


def advice_history(symbol: str, n: int = 5) -> list[dict]:
    from db import get_session
    from models import AdviceResult
    from sqlalchemy import select
    with get_session() as s:
        rows = s.execute(select(AdviceResult).where(
            AdviceResult.symbol == symbol.upper())
            .order_by(AdviceResult.ts.desc()).limit(n)).scalars().all()
        return [_row_dict(r) for r in rows]


def _row_dict(r) -> dict:
    return {"id": r.id, "ts": r.ts.isoformat() if r.ts else None,
            "is_realtime": r.is_realtime, "stance": r.stance,
            "window": {"start": r.window_start.isoformat() if r.window_start else None,
                       "end": r.window_end.isoformat() if r.window_end else None},
            "result": r.result, "reasoning": r.reasoning}


def cache_hit(symbol: str, start: str | None, end: str | None) -> dict | None:
    """有近期结果则复用(0 token)。实时/默认：最近一条实时结果在 TTL 内即用；
    历史区间：存在同 window 的结果即用(历史不变，不限龄)。命中返回其 result payload。"""
    import settings
    from db import get_session
    from models import AdviceResult
    from sqlalchemy import select
    end_dt = _parse_dt(end) or _now()
    is_realtime = (_now() - end_dt) <= _REALTIME_SLACK
    with get_session() as s:
        if is_realtime and not start:
            r = s.execute(select(AdviceResult).where(
                AdviceResult.symbol == symbol.upper(),
                AdviceResult.is_realtime.is_(True))
                .order_by(AdviceResult.ts.desc()).limit(1)).scalars().first()
            if r and r.ts and (_now() - r.ts).total_seconds() <= settings.ADVICE_CACHE_TTL:
                return r.result
            return None
        # 历史区间：匹配 window
        start_dt = _parse_dt(start)
        rows = s.execute(select(AdviceResult).where(
            AdviceResult.symbol == symbol.upper())
            .order_by(AdviceResult.ts.desc()).limit(20)).scalars().all()
        for r in rows:
            if (r.window_start and r.window_end and start_dt
                    and abs((r.window_start - start_dt).total_seconds()) < 60
                    and abs((r.window_end - end_dt).total_seconds()) < 60):
                return r.result
    return None


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
