"""价格变动多 Agent 归因 (LangChain + LangGraph)
==================================================
把系统里【已采集并 LLM 分析过】的结果作为线索，用一组角色 Agent 协同解释某标的
在某个时间窗口内的价格变动原因。

编排(参考 LangGraph 多 Agent 思路：StateGraph + 节点 + 共享状态 + 条件路由)：

    gather(采集,非LLM)
        └─(条件路由)─ 波动过小 → synth(区间波动)
                    └─ 显著波动 → [基本面 | 技术面 | 情绪资金面](并行) → critic(质疑校验) → synth(综合归因)

每个 LLM 节点用 langchain_openai.ChatOpenAI 接现有 SiliconFlow(OpenAI 兼容)，并把
生成过程(思考文本)与阶段状态通过 emit 回调流式推给前端。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TypedDict

from sqlalchemy import select

import settings
from db import get_session
from models import (IndicatorDaily, News, NewsBrief, Price, PriceAttribution,
                   T212CommunityPost)

log = logging.getLogger(__name__)

NOISE_MIN_PCT = 2.0        # 窗口 |涨跌%| 低于此值(且不足 1×ATR)视为区间波动，短路
AGENTS = ("fundamental", "technical", "sentiment", "critic", "synth")

# 限制同时打到上游 LLM 的并发数(多 Agent 并行会触发 SiliconFlow 429「系统繁忙」)。
# 默认 2：保留部分并行的"同时思考"观感，又把峰值并发从 3 降到 2；可用环境变量调。
_LLM_SEM = asyncio.Semaphore(max(1, int(os.environ.get("ATTR_LLM_CONCURRENCY", "2"))))
_LLM_RETRIES = int(os.environ.get("ATTR_LLM_RETRIES", "4"))


# ── 共享状态 ──────────────────────────────────────────────────────────────────
class AttributionState(TypedDict, total=False):
    symbol: str
    start: str
    end: str
    price_stats: dict
    clues: dict
    hyp_fundamental: str
    hyp_technical: str
    hyp_sentiment: str
    critique: str
    final: dict


# ── LLM 工厂 ─────────────────────────────────────────────────────────────────
def _llm(temperature: float = 0.3, streaming: bool = True):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.SILICONFLOW_MODEL,
        api_key=settings.SILICONFLOW_API_KEY,
        base_url=settings.SILICONFLOW_BASE_URL,
        temperature=temperature,
        streaming=streaming,
        max_retries=_LLM_RETRIES,   # 429/5xx 指数退避重试，缓解上游"系统繁忙"
        timeout=90,
    )


async def _run_llm(agent: str, system: str, human: str, emit, temperature=0.3) -> str:
    """流式调用 LLM：逐 token emit('delta')，返回完整文本。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    msgs = [SystemMessage(content=system), HumanMessage(content=human)]
    parts: list[str] = []
    err = None
    async with _LLM_SEM:                     # 限并发：避免多 Agent 同时打爆上游(429)
        try:
            async for chunk in _llm(temperature).astream(msgs):
                t = chunk.content or ""
                if t:
                    parts.append(t)
                    await _emit(emit, {"type": "delta", "agent": agent, "text": t})
        except Exception as e:
            err = e
            log.warning("attribution %s LLM 失败: %s", agent, e)
    txt = "".join(parts).strip()
    if not txt:
        # 全失败 → 干净兜底，不把报错字符串塞进后续 critic/synth 分析
        busy = err and ("429" in str(err) or "rate limit" in str(err).lower())
        note = "（该维度分析暂不可用：上游限流繁忙）" if busy else "（该维度分析暂不可用）"
        await _emit(emit, {"type": "delta", "agent": agent, "text": note})
        return note
    return txt


async def _emit(emit: Optional[Callable], ev: dict):
    if emit:
        try:
            await emit(ev)
        except Exception:
            pass


# ── 线索采集(非 LLM) ─────────────────────────────────────────────────────────
def _parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _intraday_window(sym: str, start: datetime, end: datetime) -> list:
    """按需拉分钟线，返回落在 [start,end] 内的 (ts,o,h,l,c,v) 列表(供 _price_stats)。
    区间≤2天用 5m，≤3天用 15m；yfinance 无数据时返回空。"""
    span_days = max(1, (end - start).days + 1)
    interval = "5m" if span_days <= 2 else "15m"
    try:
        yf_sym = sym
        import config
        with get_session() as db:
            for d in config.active_symbols(db):
                if d["symbol"] == sym:
                    yf_sym = d.get("yf_symbol") or sym
                    break
        from collectors.prices import _download
        frames = _download([sym], yf_map={sym: yf_sym},
                           period=f"{span_days + 1}d", interval=interval)
        df = frames.get(sym)
        if df is None or df.empty:
            return []
        out = []
        for ts, row in df.iterrows():
            pyts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if pyts.tzinfo is None:
                pyts = pyts.replace(tzinfo=timezone.utc)
            if not (start <= pyts <= end):
                continue
            c = row["Close"]
            if c != c:
                continue
            out.append((pyts, float(row["Open"]), float(row["High"]),
                        float(row["Low"]), float(c),
                        float(row.get("Volume") or 0)))
        return out
    except Exception as e:
        log.warning("intraday window %s 失败: %s", sym, e)
        return []


def _online_news(sym: str, start: datetime, end: datetime, db) -> list:
    """库内无新闻时的联网兜底：抓 Finnhub 公司新闻(按标的在线检索)存库，
    再无 sentiment 过滤地重查，返回原始头条线索(未 LLM 打分，带 online=True 标记)。"""
    try:
        from collectors import news as _news
        days = min(30, max(7, (datetime.now(timezone.utc).date() - start.date()).days + 2))
        got = _news.fetch_finnhub([sym], db, days=days)
        db.commit()
        log.info("attribution 联网新闻兜底 %s: 新增 %s 条", sym, got)
    except Exception as e:
        log.warning("attribution 联网新闻兜底失败 %s: %s", sym, e)
        return []

    def _q(win_only: bool):
        stmt = (select(News.id, News.title, News.published, News.sentiment,
                       News.llm_reason, News.source_tier, News.url)
                .where(News.symbol == sym))
        if win_only:
            stmt = stmt.where(News.published >= start, News.published <= end)
        return db.execute(stmt.order_by(News.published.desc()).limit(12)).all()

    rows = _q(True) or _q(False)   # 优先窗口内；窗口内无则取该标的最近新闻
    return [{"id": r[0], "title": r[1],
             "date": r[2].strftime("%Y-%m-%d") if r[2] else None,
             "sentiment": r[3], "llm_reason": r[4], "tier": r[5],
             "url": r[6], "online": True}
            for r in rows]


def _gather_clues(symbol: str, start: datetime, end: datetime) -> tuple[dict, dict]:
    """返回 (price_stats, clues)。库内数据为主；库内无新闻时联网(Finnhub)兜底。"""
    sym = symbol.upper()
    with get_session() as db:
        # 价格(窗口 + 前 30 交易日用于趋势/指标背景)
        rows = db.execute(
            select(Price.ts, Price.open, Price.high, Price.low, Price.close, Price.volume)
            .where(Price.symbol == sym, Price.interval == "1d",
                   Price.ts >= start - timedelta(days=45), Price.ts <= end)
            .order_by(Price.ts)).all()
        win = [r for r in rows if start <= r[0] <= end]

    # 短区间(≤3天)用按需分钟线算统计，使分钟级选区有真实数据；否则用日线窗口
    if (end - start) <= timedelta(days=3):
        intra = _intraday_window(sym, start, end)
        if len(intra) >= 2:
            win = intra
    price_stats = _price_stats(win)
    with get_session() as db:

        # 新闻(窗口内，已带 sentiment / llm_reason)
        news = db.execute(
            select(News.id, News.title, News.published, News.sentiment,
                   News.llm_reason, News.source_tier, News.relevance, News.url)
            .where(News.symbol == sym, News.published >= start, News.published <= end,
                   News.sentiment.isnot(None))
            .order_by(News.relevance.desc().nullslast(), News.published.desc())
            .limit(15)).all()
        news_clue = [{"id": n[0], "title": n[1],
                      "date": n[2].strftime("%Y-%m-%d") if n[2] else None,
                      "sentiment": n[3], "llm_reason": n[4], "tier": n[5],
                      "url": n[7]} for n in news]

        # 库内无已分析新闻 → 主动联网搜索(Finnhub)兜底，而非直接摆烂
        if not news_clue:
            news_clue = _online_news(sym, start, end, db)

        # 高信号新闻精华(窗口附近最近一条)
        brief = db.execute(
            select(NewsBrief.headline, NewsBrief.sentiment, NewsBrief.judgment,
                   NewsBrief.watch_points, NewsBrief.ts)
            .where(NewsBrief.symbol == sym, NewsBrief.ts <= end + timedelta(days=2))
            .order_by(NewsBrief.ts.desc()).limit(1)).first()
        brief_clue = ({"headline": brief[0], "sentiment": brief[1],
                       "judgment": brief[2], "watch_points": brief[3]} if brief else None)

        # 技术指标(窗口内)
        inds = db.execute(
            select(IndicatorDaily.ts, IndicatorDaily.rsi, IndicatorDaily.macd_hist,
                   IndicatorDaily.macd_cross, IndicatorDaily.vol_ratio,
                   IndicatorDaily.sma20, IndicatorDaily.sma50, IndicatorDaily.atr)
            .where(IndicatorDaily.symbol == sym,
                   IndicatorDaily.ts >= start.date(), IndicatorDaily.ts <= end.date())
            .order_by(IndicatorDaily.ts)).all()
        ind_clue = [{"date": str(i[0]), "rsi": i[1], "macd_hist": i[2],
                     "macd_cross": i[3], "vol_ratio": i[4]} for i in inds[-8:]]

        # 社区帖(窗口内，按点赞取前若干)
        posts = db.execute(
            select(T212CommunityPost.author, T212CommunityPost.content,
                   T212CommunityPost.sentiment, T212CommunityPost.likes)
            .where(T212CommunityPost.symbol == sym,
                   T212CommunityPost.published >= start, T212CommunityPost.published <= end,
                   T212CommunityPost.sentiment.isnot(None))
            .order_by(T212CommunityPost.likes.desc()).limit(6)).all()
        posts_clue = [{"content": (p[1] or "")[:160], "sentiment": p[2], "likes": p[3]}
                      for p in posts]

    # 趋势 / 资金流(复用 analysis.trend)
    trend_clue = None
    if len(rows) >= 30:
        try:
            from analysis.trend import trend_analysis
            dates = [r[0].strftime("%Y-%m-%d") for r in rows]
            tr = trend_analysis(dates, [r[4] for r in rows], [r[5] for r in rows],
                                [r[2] for r in rows], [r[3] for r in rows])
            if tr:
                trend_clue = {"trend_label": tr.get("trend_label"),
                              "regime": (tr.get("regime") or [None])[-1],
                              "money_flow_usd": tr.get("money_flow_usd"),
                              "relative_volume": tr.get("relative_volume")}
        except Exception as e:
            log.warning("attribution trend 计算失败: %s", e)

    # 情绪聚合
    sent_agg = None
    try:
        from analysis.sentiment import symbol_aggregates
        days = max(3, (end - start).days + 1)
        with get_session() as db:
            sent_agg = symbol_aggregates(db, sym, days=days)
    except Exception as e:
        log.warning("attribution sentiment 聚合失败: %s", e)

    clues = {"news": news_clue, "brief": brief_clue, "indicators": ind_clue,
             "trend": trend_clue, "community": posts_clue, "sent_agg": sent_agg}
    return price_stats, clues


def _price_stats(win: list) -> dict:
    if len(win) < 2:
        return {"n": len(win), "pct_change": 0.0}
    closes = [float(r[4]) for r in win]
    vols = [float(r[5] or 0) for r in win]
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]
    avg_vol = (sum(vols) / len(vols)) or 1.0
    notable = []
    for i in range(1, len(win)):
        notable.append({"date": win[i][0].strftime("%Y-%m-%d"),
                        "ret_pct": round(rets[i - 1], 2),
                        "vol_ratio": round(vols[i] / avg_vol, 2)})
    notable.sort(key=lambda x: abs(x["ret_pct"]), reverse=True)
    import statistics
    return {
        "n": len(win),
        "start_date": win[0][0].strftime("%Y-%m-%d"),
        "end_date": win[-1][0].strftime("%Y-%m-%d"),
        "start_close": round(closes[0], 2), "end_close": round(closes[-1], 2),
        "pct_change": round((closes[-1] - closes[0]) / closes[0] * 100, 2),
        "high": round(max(float(r[2]) for r in win), 2),
        "low": round(min(float(r[3]) for r in win), 2),
        "vol_pct": round(statistics.pstdev(rets) if len(rets) > 1 else 0.0, 2),
        "biggest_up": max(notable, key=lambda x: x["ret_pct"], default=None),
        "biggest_down": min(notable, key=lambda x: x["ret_pct"], default=None),
        "notable_days": notable[:5],
    }


# ── 节点 ──────────────────────────────────────────────────────────────────────
def _emit_of(config) -> Optional[Callable]:
    try:
        return (config or {}).get("configurable", {}).get("emit")
    except Exception:
        return None


async def node_gather(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "gather", "status": "running",
                       "detail": "采集价格 / 新闻 / 指标 / 趋势 / 情绪线索…"})
    ps, clues = await asyncio.to_thread(
        _gather_clues, state["symbol"], _parse_dt(state["start"]), _parse_dt(state["end"]))
    await _emit(emit, {"type": "phase", "agent": "gather", "status": "done",
                       "detail": f"涨跌 {ps.get('pct_change')}% · 新闻 {len(clues['news'])} 条"})
    return {"price_stats": ps, "clues": clues}


def _win_desc(s) -> str:
    ps = s["price_stats"]
    if ps.get("n", 0) < 2:
        # 价格数据缺口(≠真无波动)：提示按新闻线索归因，勿臆断"价格无变动"
        return (f"标的 {s['symbol']}：系统本地价格数据不足(仅 {ps.get('n', 0)} 个数据点)，"
                "无法计算区间涨跌；请主要依据下方新闻/情绪线索归因，不要臆断价格无变动。")
    return (f"标的 {s['symbol']}，区间 {ps.get('start_date')}~{ps.get('end_date')}，"
            f"收盘 {ps.get('start_close')}→{ps.get('end_close')}（{ps.get('pct_change')}%），"
            f"区间高低 {ps.get('high')}/{ps.get('low')}，波动 {ps.get('vol_pct')}%。")


async def node_fundamental(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "fundamental", "status": "running"})
    c = state["clues"]
    system = ("你是基本面/消息面分析师。依据【系统已采集并 LLM 分析过】的新闻(含情绪分与"
              "llm_reason)与新闻精华，找出该时段价格变动的基本面催化剂(财报/指引/评级/"
              "监管/宏观/供应链等)。只依据给定线索，指出方向(利多/利空)、证据(引用新闻标题)"
              "与把握度；无明显催化剂就直说。中文，150 字内，不要免责声明。")
    human = (_win_desc(state) + "\n新闻线索:\n" + json.dumps(c["news"], ensure_ascii=False)
             + "\n新闻精华:\n" + json.dumps(c["brief"], ensure_ascii=False))
    txt = await _run_llm("fundamental", system, human, emit)
    await _emit(emit, {"type": "phase", "agent": "fundamental", "status": "done"})
    return {"hyp_fundamental": txt}


async def node_technical(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "technical", "status": "running"})
    c = state["clues"]
    system = ("你是技术面分析师。依据技术指标(RSI/MACD 柱与金叉死叉/量比)与趋势(Kalman "
              "格局/相对成交量)判断该时段价格变动的技术驱动(突破/破位/超买超卖/放量/趋势"
              "延续或反转)。只依据给定线索，给方向与把握度。中文，130 字内，不要免责声明。")
    human = (_win_desc(state) + "\n技术指标(近几日):\n" + json.dumps(c["indicators"], ensure_ascii=False)
             + "\n趋势/资金:\n" + json.dumps(c["trend"], ensure_ascii=False)
             + "\n显著波动日:\n" + json.dumps(state["price_stats"].get("notable_days"), ensure_ascii=False))
    txt = await _run_llm("technical", system, human, emit)
    await _emit(emit, {"type": "phase", "agent": "technical", "status": "done"})
    return {"hyp_technical": txt}


async def node_sentiment(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "sentiment", "status": "running"})
    c = state["clues"]
    system = ("你是情绪/资金面分析师。依据社区帖(含情绪分)、情绪聚合(新闻/社区均分与条数)与"
              "资金流(主力净流向、相对成交量)判断散户情绪与资金动向对该时段价格的作用。只依据"
              "给定线索，给方向与把握度。中文，130 字内，不要免责声明。")
    human = (_win_desc(state) + "\n情绪聚合:\n" + json.dumps(c["sent_agg"], ensure_ascii=False)
             + "\n资金/趋势:\n" + json.dumps(c["trend"], ensure_ascii=False)
             + "\n热门社区帖:\n" + json.dumps(c["community"], ensure_ascii=False))
    txt = await _run_llm("sentiment", system, human, emit)
    await _emit(emit, {"type": "phase", "agent": "sentiment", "status": "done"})
    return {"hyp_sentiment": txt}


async def node_critic(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "critic", "status": "running"})
    system = ("你是质疑校验官。对照【真实价格方向】审视三位分析师的结论：找出彼此矛盾、或与"
              "价格走势不符之处(如利好却下跌→利好出尽/宏观拖累；无催化却大涨→情绪或资金驱动)，"
              "并给出各因素应有的权重排序。中文，150 字内，客观克制。")
    human = (_win_desc(state)
             + f"\n\n[基本面]\n{state.get('hyp_fundamental','')}"
             + f"\n\n[技术面]\n{state.get('hyp_technical','')}"
             + f"\n\n[情绪资金面]\n{state.get('hyp_sentiment','')}")
    txt = await _run_llm("critic", system, human, emit, temperature=0.2)
    await _emit(emit, {"type": "phase", "agent": "critic", "status": "done"})
    return {"critique": txt}


async def node_synth(state: AttributionState, config) -> dict:
    emit = _emit_of(config)
    await _emit(emit, {"type": "phase", "agent": "synth", "status": "running"})
    ps = state["price_stats"]
    noise = abs(ps.get("pct_change", 0)) < NOISE_MIN_PCT and not state.get("hyp_fundamental")

    system = ("你是首席分析师，产出最终【价格变动归因】。综合各方与质疑，给出 JSON："
              '{"primary":[{"cause":"","direction":"利多|利空|中性","confidence":0-100,'
              '"evidence":""}],"secondary":[{"cause":"","direction":"","confidence":0,'
              '"evidence":""}],"narrative":"一段中文叙述","confidence":0-100,'
              '"caveats":"一句话提示"}。只输出 JSON。cause 精炼，evidence 引用新闻标题或指标。'
              "若价格几乎无变动，primary 给'区间波动/无显著驱动'。")
    if ps.get("n", 0) < 2 and not state.get("hyp_fundamental"):
        # 价格数据缺口且无新闻线索 → 诚实说"数据不足"，不编造"区间波动/无显著驱动"
        human = (f"标的 {state['symbol']}：系统未取到足够价格数据(仅 {ps.get('n', 0)} 点)，"
                 "也未检索到可用新闻线索。请诚实产出【数据不足，暂无法归因】："
                 "primary 给 cause='数据不足，暂无法归因'、direction='中性'、confidence≤30、"
                 "evidence 说明缺哪些数据；caveats 提示可稍后重试或检查标的代码。不要臆造区间波动结论。")
    elif noise:
        human = _win_desc(state) + "\n该时段价格几乎无显著变动，按区间波动归因。"
    else:
        human = (_win_desc(state)
                 + f"\n\n[基本面]\n{state.get('hyp_fundamental','')}"
                 + f"\n[技术面]\n{state.get('hyp_technical','')}"
                 + f"\n[情绪资金面]\n{state.get('hyp_sentiment','')}"
                 + f"\n[质疑校验]\n{state.get('critique','')}")
    from langchain_core.messages import HumanMessage, SystemMessage
    try:
        async with _LLM_SEM:
            resp = await _llm(0.2, streaming=False).ainvoke(
                [SystemMessage(content=system), HumanMessage(content=human)])
        data = _parse_json(resp.content)
    except Exception as e:
        log.warning("attribution synth 失败: %s", e)
        data = None
    if not data:
        data = {"primary": [{"cause": "无法生成结构化归因", "direction": "中性",
                             "confidence": 0, "evidence": ""}],
                "secondary": [], "narrative": "综合归因生成失败，请重试。",
                "confidence": 0, "caveats": "历史归因仅供参考，非投资建议。"}
    data.setdefault("caveats", "历史数据可能会骗人；归因仅供参考，非投资建议。")
    data["evidence_news"] = [{"id": n["id"], "title": n["title"],
                              "url": n.get("url"), "date": n.get("date"),
                              "online": n.get("online", False)}
                             for n in state["clues"].get("news", [])[:8]]
    data["price_stats"] = ps
    # 先落库(在发 result 之前，避免 SSE 客户端收到结果即断开→任务被取消导致漏存)
    agents_txt = {"fundamental": state.get("hyp_fundamental"), "technical": state.get("hyp_technical"),
                  "sentiment": state.get("hyp_sentiment"), "critique": state.get("critique")}
    try:
        await asyncio.to_thread(_save, state["symbol"], state["start"], state["end"],
                                data, agents_txt, ps.get("pct_change"))
    except Exception as e:
        log.warning("attribution 落库失败: %s", e)
    await _emit(emit, {"type": "phase", "agent": "synth", "status": "done"})
    await _emit(emit, {"type": "result", "data": data})
    return {"final": data}


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        t = t.split("```")[1].removeprefix("json").strip() if t.count("```") >= 2 else t
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            return None
    return None


# ── 图 ────────────────────────────────────────────────────────────────────────
def _route_after_gather(state: AttributionState):
    ps = state["price_stats"]
    has_news = bool((state.get("clues") or {}).get("news"))
    thin = ps.get("n", 0) < 2 or abs(ps.get("pct_change", 0)) < NOISE_MIN_PCT
    if thin:
        # 价格数据不足/波动过小，但有新闻线索(含联网抓取) → 仍走基本面+情绪分析(用新闻解释)，
        # 不再直接判"区间波动"。真的既无价格又无新闻才短路到 synth 诚实说明。
        return ["agent_fundamental", "agent_sentiment"] if has_news else ["agent_synth"]
    return ["agent_fundamental", "agent_technical", "agent_sentiment"]


_GRAPH = None


def _build_graph():
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    from langgraph.graph import END, START, StateGraph
    g = StateGraph(AttributionState)
    g.add_node("gather", node_gather)
    g.add_node("agent_fundamental", node_fundamental)
    g.add_node("agent_technical", node_technical)
    g.add_node("agent_sentiment", node_sentiment)
    g.add_node("agent_critic", node_critic)
    g.add_node("agent_synth", node_synth)
    g.add_edge(START, "gather")
    g.add_conditional_edges("gather", _route_after_gather,
                            ["agent_fundamental", "agent_technical",
                             "agent_sentiment", "agent_synth"])
    g.add_edge("agent_fundamental", "agent_critic")
    g.add_edge("agent_technical", "agent_critic")
    g.add_edge("agent_sentiment", "agent_critic")
    g.add_edge("agent_critic", "agent_synth")
    g.add_edge("agent_synth", END)
    _GRAPH = g.compile()
    return _GRAPH


# ── 持久化 / 缓存复用 ─────────────────────────────────────────────────────────
def _load_cached(symbol: str, start: str, end: str) -> Optional[dict]:
    with get_session() as db:
        row = db.execute(
            select(PriceAttribution).where(
                PriceAttribution.symbol == symbol.upper(),
                PriceAttribution.start_ts == _parse_dt(start),
                PriceAttribution.end_ts == _parse_dt(end))
            .order_by(PriceAttribution.created_at.desc())).scalars().first()
        if row and row.result:
            return {"result": row.result, "agents": row.agents or {},
                    "created_at": row.created_at.isoformat()}
    return None


def _save(symbol: str, start: str, end: str, final: dict, agents: dict,
          pct_change: Optional[float]):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    with get_session() as db:
        db.execute(pg_insert(PriceAttribution).values(
            symbol=symbol.upper(), start_ts=_parse_dt(start), end_ts=_parse_dt(end),
            pct_change=pct_change, result=final, agents=agents
        ).on_conflict_do_update(
            index_elements=["symbol", "start_ts", "end_ts"],
            set_={"result": final, "agents": agents, "pct_change": pct_change,
                  "created_at": datetime.now(timezone.utc)}))


async def _replay_cached(cached: dict, emit):
    """命中缓存：快速回放各 Agent 的历史思考 + 最终结果(带 cached 标记)。"""
    agents = cached.get("agents") or {}
    await _emit(emit, {"type": "phase", "agent": "gather", "status": "done", "detail": "命中历史缓存"})
    for key in ("fundamental", "technical", "sentiment", "critic"):
        akey = "critique" if key == "critic" else key
        await _emit(emit, {"type": "phase", "agent": key, "status": "done"})
        if agents.get(akey):
            await _emit(emit, {"type": "delta", "agent": key, "text": agents[akey]})
    await _emit(emit, {"type": "phase", "agent": "synth", "status": "done"})
    data = dict(cached["result"])
    data["cached"] = True
    data["created_at"] = cached.get("created_at")
    await _emit(emit, {"type": "result", "data": data})


async def run_attribution(symbol: str, start: str, end: str,
                          emit: Optional[Callable] = None, force: bool = False) -> dict:
    """跑完整多 Agent 归因。命中(标的+时段)缓存则复用不重跑。emit 为流式回调。"""
    if not settings.llm_enabled:
        data = {"primary": [], "secondary": [], "narrative": "LLM 未配置，无法归因。",
                "confidence": 0, "caveats": ""}
        await _emit(emit, {"type": "result", "data": data})
        return data
    if not force:
        cached = await asyncio.to_thread(_load_cached, symbol, start, end)
        if cached:
            await _replay_cached(cached, emit)
            return cached["result"]
    graph = _build_graph()
    state = {"symbol": symbol.upper(), "start": start, "end": end}
    out = await graph.ainvoke(state, config={"configurable": {"emit": emit}})
    return out.get("final", {})   # 落库已在 node_synth 内完成(发 result 之前)
