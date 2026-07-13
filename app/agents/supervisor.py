"""Supervisor Agent：动态委派的多 Agent 编排（替换 equity_research_agents 的手写 if/elif）。

Qwen 工具循环：把能力总线按 risk 过滤后的工具（含 research/attribution/quant_advisor 等子 Agent）
交给 supervisor，让它自主决定调哪个、调几次，最后综合成结论。全程 `_emit` 流式（复用现有
SSE 事件协议 phase/delta/result）。运行前 Recall 研究档案、运行后 Distill 回写（反思记忆）。

写权限由 `allow_write` 控制：交互式默认 False（只读分析）；托管在预算内传 True（Phase 5/6）。
"""
from __future__ import annotations

import asyncio
import json
import logging

import settings
from agents import runlog
from analysis import thesis as thesis_mod

log = logging.getLogger(__name__)

MAX_ROUNDS = int(__import__("os").environ.get("SUPERVISOR_MAX_ROUNDS", "6"))

_SYSTEM = (
    "你是首席投研 supervisor，统筹多个领域专精子 Agent 为用户解决关于某标的的问题。"
    "你有一组工具：get_market_data/get_indicators/get_options/get_sentiment(确定性数据)、"
    "research(研究子 Agent)、attribution(价格归因多 Agent 子图)、quant_advisor(量化策略顾问)、"
    "以及量化状态查询。**数字只引用工具返回值，绝不臆造**。"
    "工作方式：先判断需要哪些子 Agent/数据 → 逐个调用 → 综合。"
    "若给了『既有研究档案』，在其基础上进化(确认/推翻/细化)，并对到期验证点给出结论。"
    "最终用中文给出有立场、可追溯、多角度的分析，区分已证实与指控，估值给情景区间而非买卖建议，"
    "结尾给『下一验证点』。财报日期须按 market_data.earnings 的 status 表述(estimated 标注预计)。"
    "不得出现买入/卖出/加减仓这类操作建议。"
)


def _tool_specs(allow_write: bool) -> list[dict]:
    import tools
    risk = {"read", "write_order", "write_strategy"} if allow_write else {"read"}
    return tools.specs(risk=risk)


async def run_supervisor(symbol: str, query: str, *, mode: str = "interactive",
                         emit=None, allow_write: bool = False) -> dict:
    """跑一次 supervisor。返回 {answer, decision, run_id, tool_calls}。"""
    import tools
    from price_attribution_agents import _emit, _LLM_SEM
    from analysis.compliance import enforce_compliance
    from analysis.research import _llm_client

    sym = symbol.split("_")[0].upper()
    if not settings.llm_enabled:
        data = {"answer": "LLM 未配置，无法进行 Agent 分析。", "decision": None,
                "run_id": None, "tool_calls": []}
        await _emit(emit, {"type": "result", "data": data})
        return data

    await _emit(emit, {"type": "phase", "agent": "supervisor", "status": "running",
                       "detail": "读取研究档案与规划委派"})
    prior, recall_text = await asyncio.to_thread(_recall_sync, sym)

    client = _llm_client()
    tool_specs = _tool_specs(allow_write)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "\n\n".join(x for x in [
            f"标的：{sym}\n用户问题：{query}", recall_text] if x)},
    ]
    tool_calls_log: list[dict] = []
    timeline: list[dict] = [runlog.event(
        "start", agent="supervisor",
        detail=("已载入研究档案，开始规划" if prior else "无历史档案，冷启动规划"))]
    decisions: list[dict] = []          # 写类工具（下单/改策略）的落地记录
    answer: str | None = None

    for _ in range(max(1, MAX_ROUNDS)):
        try:
            async with _LLM_SEM:
                resp = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=settings.SILICONFLOW_TOOL_MODEL,
                    messages=messages, tools=tool_specs, tool_choice="auto",
                    temperature=0.3, max_tokens=2000, timeout=90)
        except Exception as e:                   # noqa: BLE001
            log.warning("supervisor LLM 调用失败 %s: %s", sym, e)
            timeline.append(runlog.event("thinking", agent="supervisor",
                                         text=f"（LLM 调用失败：{e}）"))
            break
        msg = resp.choices[0].message
        if (msg.content or "").strip():          # 记录 supervisor 的可见推理
            timeline.append(runlog.event("thinking", agent="supervisor",
                                         text=(msg.content or "").strip()[:2000]))
        calls = getattr(msg, "tool_calls", None)
        if not calls:
            answer = (msg.content or "").strip()
            break
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
            await _emit(emit, {"type": "phase", "agent": name, "status": "running",
                               "detail": f"委派 {name}({_arg_hint(args)})"})
            out = await tools.dispatch(name, args)
            tool_calls_log.append({"tool": name, "args": args})
            is_write = name in ("create_order_intent", "adjust_strategy")
            timeline.append(runlog.event(
                "decision" if is_write else "tool",
                tool=name, args=args, result=_summ(out)))
            if is_write:
                decisions.append({"tool": name, "args": args, "out": out})
            await _emit(emit, {"type": "phase", "agent": name, "status": "done"})
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": json.dumps(out, ensure_ascii=False,
                                                   default=str)[:6000]})

    # 未直接给出答案（轮次用尽或中途异常）→ 基于已收集内容综合一次
    if not answer:
        answer = await _final_synthesis(client, messages, emit)

    answer = enforce_compliance(answer)
    timeline.append(runlog.event("answer", agent="supervisor", text=answer))
    await _emit(emit, {"type": "delta", "agent": "supervisor", "text": answer})

    # 落库 AgentRun（先存拿到 run_id，供 thesis 快照回指与托管追加执行步骤）
    run_id = await asyncio.to_thread(
        runlog.save_run, sym, mode, query,
        timeline=timeline, tool_calls=tool_calls_log,
        decision=(decisions or None),
        outcome={"answer_len": len(answer), "n_tools": len(tool_calls_log),
                 "n_decisions": len(decisions)})
    # 反思：把本次结论蒸馏进研究档案
    try:
        await thesis_mod.distill(sym, answer, prior=prior,
                                 source_run_id=run_id, emit=emit)
    except Exception as e:                       # noqa: BLE001
        log.warning("supervisor distill %s 失败: %s", sym, e)

    data = {"answer": answer, "decision": (decisions or None), "run_id": run_id,
            "tool_calls": tool_calls_log}
    await _emit(emit, {"type": "phase", "agent": "supervisor", "status": "done"})
    await _emit(emit, {"type": "result", "data": data})
    return data


async def _final_synthesis(client, messages, emit) -> str:
    from price_attribution_agents import _emit
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.SILICONFLOW_MODEL,
            messages=messages + [{"role": "user",
                                  "content": "请基于以上工具结果给出最终中文分析，结尾给下一验证点。"}],
            temperature=0.3, max_tokens=2200, timeout=120)
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:                       # noqa: BLE001
        log.warning("supervisor 综合失败: %s", e)
        await _emit(emit, {"type": "error", "message": f"综合阶段失败: {e}"})
        return "分析未能完成（综合阶段调用失败）。"


def _arg_hint(args: dict) -> str:
    return str(args.get("symbol") or args.get("query") or args.get("keyword")
               or args.get("code") or "")[:40]


def _summ(out) -> str:
    """工具返回值的时间轴摘要（截断，避免 transcript 膨胀）。"""
    try:
        s = json.dumps(out, ensure_ascii=False, default=str)
    except Exception:
        s = str(out)
    return s[:400]


def _recall_sync(symbol: str):
    try:
        return thesis_mod.recall(symbol)
    except Exception as e:                       # noqa: BLE001
        log.warning("recall %s 失败: %s", symbol, e)
        return None, ""
