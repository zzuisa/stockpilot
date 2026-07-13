"""领域子 Agent（注册为能力总线工具，供 supervisor 委派）。

Phase 3 引入 `quant_advisor`：读某标的的量化策略实时状态 + 市场快照，产出**参数调整建议**
（只读、不执行）。真正落地改参在 Phase 5 的 `adjust_strategy` 写工具（过风控）。
`research` / `attribution` 已是注册表里的多步子 Agent，无需重复包装。
"""
from __future__ import annotations

import json
import logging

from tools.registry import Tool, register

log = logging.getLogger(__name__)

_ADVISOR_SYSTEM = (
    "你是量化策略顾问。给你某标的正在运行的策略状态(params/持仓/盈亏/胜率/最近动作)与市场快照，"
    "请诊断当前参数是否合理，并给出**具体的参数调整建议**(哪个参数、从多少改到多少、依据是什么)。"
    "只诊断与建议，不执行下单/改参。关注：buy_mode/rsi_buy/rsi_sell/stop_loss/profit_pct/"
    "budget_ratio/max_trades_day 等。若当前表现良好，明说维持不动。用中文，简洁分点。"
)


async def _quant_advisor(symbol: str) -> dict:
    """返回 {symbol, advice, proposed_params?}。纯建议，不落地。"""
    from tools import dispatch
    from price_attribution_agents import _run_llm, _parse_json

    sym = symbol.split("_")[0].upper()
    status = await dispatch("get_strategy_status", {"symbol": sym})
    md = await dispatch("get_market_data", {"symbol": sym})
    human = (f"标的 {sym}\n\n策略实时状态：\n{json.dumps(status, ensure_ascii=False, default=str)[:3000]}"
             f"\n\n市场快照：\n{json.dumps(md, ensure_ascii=False, default=str)[:3000]}"
             "\n\n请诊断并给出参数调整建议。若有明确改动，最后附一行 JSON："
             '{"proposed_params":{"参数":新值}}（只列要改的键；无需改动则省略）。')
    text = await _run_llm("quant_advisor", _ADVISOR_SYSTEM, human, None, temperature=0.3)
    proposed = None
    # 从结尾抽取 proposed_params（若模型给了）
    for line in reversed(text.splitlines()):
        if "proposed_params" in line:
            d = _parse_json(line) or {}
            proposed = d.get("proposed_params")
            break
    return {"symbol": sym, "advice": text.strip(), "proposed_params": proposed}


def _register() -> None:
    from tools.registry import REGISTRY
    if "quant_advisor" not in REGISTRY:
        register(Tool(
            "quant_advisor",
            "量化策略顾问子 Agent：诊断某标的运行中的策略并给出参数调整建议(只读，不落地)。",
            {"type": "object", "properties": {"symbol": {"type": "string"}},
             "required": ["symbol"]},
            _quant_advisor, risk="read", domain="quant"))


_register()
