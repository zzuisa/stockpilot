"""股票研究 Agent（LangChain + LangGraph）
===========================================
把 `equity_reasearch_agent_skill.md`（角色 / 分析立场 / 三层数据纪律 / 意图模板 / 护栏）
落成一个嵌进现有 LangGraph 的研究 Agent：用户对某标的自由提问 → 意图分类 → 选模板
→ 分层产出（确定性 `market_data` 注入 + 库内定性线索检索 + LLM 推理），流式展示。

核心纪律：**数字来自 `analysis/market_data.py`，LLM 只推理不生成数字**；`[enforce in code]`
护栏（新鲜度 flag、合规后处理、cite-or-abstain）在管线里做，不靠提示。

大量复用 `price_attribution_agents.py`：`_run_llm`(token 流式)、`_emit`、`_LLM_SEM`(限并发防 429)、
`_gather_clues`(库内新闻/指标/趋势/情绪聚合)、`run_attribution`(意图 A 移动归因直接复用)。
"""
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, TypedDict

from sqlalchemy import select

import settings
from analysis import market_data as mdata
from db import get_session
from models import ResearchQuery
from price_attribution_agents import (_emit, _gather_clues, _LLM_SEM,
                                     _parse_json, _run_llm, run_attribution)

log = logging.getLogger(__name__)

RESEARCH_CACHE_TTL = float(os.environ.get("RESEARCH_CACHE_TTL", str(6 * 3600)))  # 6h

# 研究 Agent 的系统提示：运行时读 skill 全文（已复制进 app/prompts/ 以进 Docker 构建上下文）
_SKILL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "prompts", "equity_research_skill.md")


def _skill_prompt() -> str:
    try:
        with open(_SKILL_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        log.warning("研究 skill 读取失败(%s)，用内置精简版: %s", _SKILL_PATH, e)
        return ("你是股票研究分析师。数字只能来自注入的 market_data，绝不臆造；"
                "定性信息用库内新闻线索。给有立场、可追溯、多角度的分析，区分已证实与指控，"
                "分析师共识注意时效，估值给 bear/base/bull 情景区间而非买卖建议，末尾给下一验证点。")


# ── 共享状态 ──────────────────────────────────────────────────────────────────
class ResearchState(TypedDict, total=False):
    symbol: str
    query: str
    intent: dict            # {template, window_days, note}
    market_data: dict
    result: dict


_TEMPLATES = ("attribution", "valuation", "deep_review", "comparison", "earnings", "quick_fact")


# ── 意图分类 ──────────────────────────────────────────────────────────────────
async def classify_intent(symbol: str, query: str, emit=None) -> dict:
    await _emit(emit, {"type": "phase", "agent": "intent", "status": "running",
                       "detail": "识别问题意图"})
    system = (
        "你是股票研究问题的意图分类器。把用户对某标的的问题归入一种模板并返回 JSON："
        '{"template":"attribution|valuation|deep_review|comparison|earnings|quick_fact",'
        '"window_days":整数(仅 attribution 用，问“最近/为什么涨跌”默认 20),'
        '"note":"一句话说明"}。判定规则：'
        "为什么涨/跌/大跌/异动→attribution；估值/合理区间/入场/贵不贵/值不值→valuation；"
        "深度分析/基本面体检/全面看→deep_review；A对比B/谁更好→comparison；"
        "财报/业绩解读→earnings；下次财报几号/现价/PE 等快问快答→quick_fact。只输出 JSON。")
    human = f"标的：{symbol}\n问题：{query}"
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=settings.SILICONFLOW_TOOL_MODEL,
                        api_key=settings.SILICONFLOW_API_KEY,
                        base_url=settings.SILICONFLOW_BASE_URL,
                        temperature=0.0, streaming=False, max_retries=3, timeout=45)
        async with _LLM_SEM:
            resp = await llm.ainvoke([SystemMessage(content=system),
                                     HumanMessage(content=human)])
        data = _parse_json(resp.content) or {}
    except Exception as e:
        log.warning("研究意图分类失败: %s", e)
        data = {}
    tmpl = data.get("template")
    if tmpl not in _TEMPLATES:
        tmpl = "valuation"          # 兜底：走信息量最大的估值模板
        data["note"] = data.get("note") or "意图不明确，按估值/多角度分析处理"
    intent = {"template": tmpl,
              "window_days": int(data.get("window_days") or 20),
              "note": data.get("note") or ""}
    await _emit(emit, {"type": "phase", "agent": "intent", "status": "done",
                       "detail": f"{tmpl}｜{intent['note']}", "data": intent})
    return intent


# ── 线索渲染 ──────────────────────────────────────────────────────────────────
def _clues_text(clues: dict) -> str:
    lines = []
    for n in (clues.get("news") or [])[:10]:
        r = f"｜研判:{n['llm_reason']}" if n.get("llm_reason") else ""
        lines.append(f"- [{n.get('date')}] {n.get('title')}（情绪 {n.get('sentiment')}{r}）")
    news_txt = "\n".join(lines) if lines else "（窗口内无已分析新闻）"
    brief = clues.get("brief")
    brief_txt = (f"高信号精华：{brief.get('headline')}｜判断 {brief.get('judgment')}｜"
                 f"关注点 {brief.get('watch_points')}" if brief else "")
    sent = clues.get("sent_agg")
    sent_txt = f"情绪聚合：{json.dumps(sent, ensure_ascii=False)}" if sent else ""
    trend = clues.get("trend")
    trend_txt = f"趋势/资金：{json.dumps(trend, ensure_ascii=False)}" if trend else ""
    return "\n".join(x for x in ["【近期新闻线索（定性，仅供推理，勿臆造数字）】",
                                 news_txt, brief_txt, sent_txt, trend_txt] if x)


_TEMPLATE_HINT = {
    "valuation": ("按【B 估值与入场区间】模板：现价与背景 → 基本面(最新季，取自 market_data) → "
                  "估值(historic/forward 倍数、市场price-in 了什么) → 分析师观点(用 freshness "
                  "区分新鲜/滞后，共识滞后必须点破) → **bear/base/bull 情景区间表**(每档写清假设依据) → "
                  "关键风险 → 下一催化剂。给情景不给买卖建议。"),
    "deep_review": ("按【C 深度体检】：融合基本面 + 近期事件时间线(定性线索) + 情绪，用 A/B 两模板做分节；"
                    "分已证实 vs 指控；末尾下一验证点。"),
    "earnings": ("按【E 财报解读】：beat/miss vs 一致预期 → 指引变化 → 市场反应 → 对投资逻辑的含义；"
                 "数字取自 market_data，缺失就说未获取。"),
    "comparison": ("按【D 横向对比】：就估值/增长/主题暴露分档说明该标的所处位置(暂以单标的视角，"
                   "缺对比标的数据则说明局限)。"),
}


# ── 节点 ──────────────────────────────────────────────────────────────────────
async def node_quickfact(state: ResearchState, emit) -> dict:
    """F 快问快答 fast path：直接从 market_data 取数，不进 LLM。"""
    await _emit(emit, {"type": "phase", "agent": "quick_fact", "status": "running"})
    md = state["market_data"]
    ans = mdata.quick_fact_answer(md, state["query"]) or (
        f"{md['symbol']} 现价约 {md['quote'].get('live_price')}，"
        f"下次财报 {md.get('earnings_date')}，预期 PE {md['fundamentals'].get('forward_pe')}。")
    ans = mdata.enforce_compliance(ans)
    await _emit(emit, {"type": "delta", "agent": "quick_fact", "text": ans})
    await _emit(emit, {"type": "phase", "agent": "quick_fact", "status": "done"})
    return {"template": "quick_fact", "answer": ans, "market_data": md,
            "freshness": md.get("freshness")}


async def node_analyze(state: ResearchState, emit) -> dict:
    """B/C/D/E：确定性 market_data + 库内定性线索 → LLM 流式推理 → 合规后处理。"""
    md = state["market_data"]
    tmpl = state["intent"]["template"]
    sym = state["symbol"]
    await _emit(emit, {"type": "phase", "agent": "research", "status": "running",
                       "detail": "检索线索并分析"})
    # 定性线索：近 14 天库内已分析新闻/情绪/趋势
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=14)
    try:
        _, clues = await asyncio.to_thread(_gather_clues, sym, start, end)
    except Exception as e:
        log.warning("研究线索采集失败: %s", e)
        clues = {}

    fresh = md.get("freshness") or {}
    fresh_line = (f"⚠新鲜度：{fresh.get('note')}（现价 {fresh.get('live_price')} vs 均价目标 "
                  f"{fresh.get('blended_target')}，背离 {fresh.get('divergence_pct')}%）"
                  if fresh.get("flag") else "")
    human = "\n\n".join(x for x in [
        f"用户问题：{state['query']}",
        f"本次意图模板：{tmpl}。{_TEMPLATE_HINT.get(tmpl, '')}",
        mdata.market_data_text(md),
        fresh_line,
        _clues_text(clues),
        "要求：数字只引用 market_data，缺失明说“未获取”；分析师共识注意时效；区分已证实与指控；"
        "结尾给“下一验证点”。财报日期只用 market_data.earnings：status=confirmed 才可写“下次财报”，"
        "status=estimated 必须写“预计/待官方确认”，绝不把 last_reported 或任何早于 as_of 的日期"
        "表述为“下次/即将”的财报。用中文，Markdown 分节，情景表用表格。不得出现买入/卖出/加减仓建议。",
    ] if x)

    text = await _run_llm("research", _skill_prompt(), human, emit, temperature=0.3)
    text = mdata.enforce_compliance(text)
    await _emit(emit, {"type": "phase", "agent": "research", "status": "done"})
    return {"template": tmpl, "answer": text, "market_data": md,
            "freshness": md.get("freshness")}


async def node_attribution(state: ResearchState, emit) -> dict:
    """A 移动归因：直接复用整套多 Agent 归因子图（含其流式与落库）。"""
    sym = state["symbol"]
    days = state["intent"].get("window_days", 20)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%d %H:%M:%S"
    attr = await run_attribution(sym, start.strftime(fmt), end.strftime(fmt), emit=emit)
    # 把归因结果转成统一的 answer markdown（前端与其它模板一致渲染）
    parts = [f"### {sym} 近 {days} 天价格变动归因", attr.get("narrative", "")]
    if attr.get("primary"):
        parts.append("\n**主要驱动：**")
        for p in attr["primary"]:
            parts.append(f"- {p.get('cause')}（{p.get('direction')}，置信 {p.get('confidence')}）"
                         f"{'：' + p.get('evidence') if p.get('evidence') else ''}")
    if attr.get("caveats"):
        parts.append(f"\n> {attr['caveats']}")
    answer = mdata.enforce_compliance("\n".join(parts))
    return {"template": "attribution", "answer": answer, "attribution": attr,
            "market_data": state["market_data"], "freshness": state["market_data"].get("freshness")}


# ── 编排入口 ──────────────────────────────────────────────────────────────────
def _qhash(query: str) -> str:
    return hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()[:16]


def _load_cached(symbol: str, qhash: str) -> Optional[dict]:
    try:
        with get_session() as db:
            row = db.execute(
                select(ResearchQuery).where(ResearchQuery.symbol == symbol.upper(),
                                           ResearchQuery.query_hash == qhash)
                .order_by(ResearchQuery.created_at.desc())).scalars().first()
            if row and row.result:
                age = (datetime.now(timezone.utc) - row.created_at.replace(tzinfo=timezone.utc)).total_seconds()
                if age <= RESEARCH_CACHE_TTL:
                    return {"result": row.result, "created_at": row.created_at.isoformat()}
    except Exception as e:
        log.warning("研究缓存读取失败: %s", e)
    return None


def _save(symbol: str, query: str, qhash: str, template: str, result: dict):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    safe = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    with get_session() as db:
        db.execute(pg_insert(ResearchQuery).values(
            symbol=symbol.upper(), query=query, query_hash=qhash,
            template=template, result=safe
        ).on_conflict_do_update(
            index_elements=["symbol", "query_hash"],
            set_={"query": query, "template": template, "result": safe,
                  "created_at": datetime.now(timezone.utc)}))


async def run_research(symbol: str, query: str, emit: Optional[Callable] = None,
                       force: bool = False) -> dict:
    """研究 Agent 主入口：意图路由 → 模板 → 流式产出。命中(标的+问题)缓存则复用。"""
    sym = symbol.split("_")[0].upper()
    if not settings.llm_enabled:
        data = {"template": "none", "answer": "LLM 未配置，无法进行研究分析。",
                "market_data": None, "freshness": None}
        await _emit(emit, {"type": "result", "data": data})
        return data

    qhash = _qhash(query)
    if not force:
        cached = await asyncio.to_thread(_load_cached, sym, qhash)
        if cached:
            data = dict(cached["result"])
            data["cached"] = True
            data["created_at"] = cached.get("created_at")
            await _emit(emit, {"type": "phase", "agent": "intent", "status": "done",
                               "detail": "命中历史缓存"})
            if data.get("answer"):
                await _emit(emit, {"type": "delta", "agent": data.get("template", "research"),
                                   "text": data["answer"]})
            await _emit(emit, {"type": "result", "data": data})
            return data

    intent = await classify_intent(sym, query, emit)
    # market_data 确定性层（yfinance .info 慢 → 放线程池，含 1h 缓存）
    md = await asyncio.to_thread(mdata.build_market_data, sym)
    if md.get("freshness", {}) and md["freshness"].get("flag"):
        await _emit(emit, {"type": "phase", "agent": "freshness", "status": "done",
                           "detail": md["freshness"]["note"], "data": md["freshness"]})
    state: ResearchState = {"symbol": sym, "query": query, "intent": intent, "market_data": md}

    tmpl = intent["template"]
    if tmpl == "quick_fact":
        result = await node_quickfact(state, emit)
    elif tmpl == "attribution":
        result = await node_attribution(state, emit)
    else:
        result = await node_analyze(state, emit)

    result["intent"] = intent
    # 先落库再发 result（避免 SSE 客户端收到结果即断开→任务取消漏存）
    try:
        await asyncio.to_thread(_save, sym, query, qhash, result.get("template", tmpl), result)
    except Exception as e:
        log.warning("研究结果落库失败: %s", e)
    await _emit(emit, {"type": "result", "data": result})
    return result
