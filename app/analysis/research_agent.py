"""Qwen 工具代理：自主调用富途 skills 组装多源情报，供 DeepSeek 最终分析。

用 SiliconFlow 的 Qwen 模型(function-calling)在若干轮内决定调用哪些 Futu 工具
(search_news/snapshot/rating_summary/capital_flow/company_profile)，汇总成情报摘要。
仅在非美股 + Futu 可用时被调用(见 analysis/advice.py)；不可用则整段跳过(0 token)。
"""
import asyncio
import json
import logging

import settings
from analysis import futu_skills

log = logging.getLogger(__name__)

_TOOLS = [
    {"type": "function", "function": {
        "name": "search_news",
        "description": "按关键词搜索富途资讯(新闻/公告/评级)，用于了解该标的近期消息面",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "公司名或代码关键词"},
            "sub_type": {"type": "string", "enum": ["NEWS", "NOTICE", "RATING", "ALL"]},
        }, "required": ["keyword"]}}},
    {"type": "function", "function": {
        "name": "snapshot",
        "description": "获取富途标的快照(现价/涨跌/估值等)。参数 code 如 HK.00700 / US.AAPL",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "rating_summary",
        "description": "获取分析师评级汇总。code 如 HK.00700",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "capital_flow",
        "description": "获取主力资金流向。code 如 HK.00700",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "company_profile",
        "description": "获取公司概况。code 如 HK.00700",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}}},
]

_DISPATCH = {
    "search_news": lambda a: futu_skills.search_news(
        a.get("keyword", ""), a.get("sub_type", "NEWS")),
    "snapshot": lambda a: futu_skills.snapshot(a.get("code", "")),
    "rating_summary": lambda a: futu_skills.rating_summary(a.get("code", "")),
    "capital_flow": lambda a: futu_skills.capital_flow(a.get("code", "")),
    "company_profile": lambda a: futu_skills.company_profile(a.get("code", "")),
}

_SYSTEM = (
    "你是股票研究员，负责用富途工具为分析师收集某标的的多源情报(新闻/公告/评级/资金流/快照)。"
    "根据标的与市场，主动调用合适的工具(港股/A股/美股用 code 如 HK.00700/US.AAPL；德股等无 code 时"
    "只用 search_news 关键词搜)。工具返回可能是 {'unavailable':true} 表示暂不可用，跳过即可。"
    "收集足够后，用中文给出 250 字内的情报摘要：消息面要点、评级/目标价、资金动向、需注意的风险。"
    "只陈述工具查到的事实，不编造。"
)


async def gather_dossier(symbol: str, market: str | None,
                         keyword: str | None = None, emit=None) -> dict:
    """返回 {summary, tool_outputs}。Futu 不可用或未配置 Qwen 则返回空 summary。"""
    if not futu_skills.available():
        return {"summary": "", "tool_outputs": []}
    import price_attribution_agents as paa
    from analysis.research import _llm_client
    kw = keyword or symbol
    code = None
    try:
        import config
        with __import__("db").get_session() as s:
            for d in config.active_symbols(s):
                if d["symbol"] == symbol:
                    code = config.futu_code(d)
                    break
    except Exception:
        pass

    client = _llm_client()
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content":
            f"标的 {symbol}（市场 {market}，富途代码 {code or '无(仅关键词搜)'}，"
            f"搜索关键词 {kw}）。请收集其近期多源情报并给出摘要。"},
    ]
    tool_outputs: list[dict] = []
    for _ in range(max(1, settings.FUTU_AGENT_MAX_ROUNDS)):
        try:
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=settings.SILICONFLOW_TOOL_MODEL,
                messages=messages, tools=_TOOLS, tool_choice="auto",
                temperature=0.2, max_tokens=1200, timeout=90)
        except Exception as e:
            log.warning("qwen 研究员调用失败 %s: %s", symbol, e)
            break
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None)
        if not calls:
            return {"summary": (msg.content or "").strip(),
                    "tool_outputs": tool_outputs}
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name,
                                                      "arguments": c.function.arguments}}
                                        for c in calls]})
        for c in calls:
            name = c.function.name
            try:
                args = json.loads(c.function.arguments or "{}")
            except Exception:
                args = {}
            await paa._emit(emit, {"type": "phase", "agent": "futu",
                                   "status": "running",
                                   "detail": f"富途工具 {name}({args.get('keyword') or args.get('code') or ''})"})
            fn = _DISPATCH.get(name)
            out = await asyncio.to_thread(fn, args) if fn else {"error": "unknown tool"}
            tool_outputs.append({"tool": name, "args": args, "out": out})
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": json.dumps(out, ensure_ascii=False)[:4000]})
    # 轮次用尽：让模型基于已收集内容直接总结一次
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.SILICONFLOW_TOOL_MODEL,
            messages=messages + [{"role": "user", "content": "请基于以上工具结果给出中文情报摘要。"}],
            temperature=0.2, max_tokens=800, timeout=90)
        return {"summary": (resp.choices[0].message.content or "").strip(),
                "tool_outputs": tool_outputs}
    except Exception as e:
        log.warning("qwen 研究员总结失败 %s: %s", symbol, e)
        return {"summary": "", "tool_outputs": tool_outputs}
