"""Agent 运行时间轴的落库/追加（手动 + 托管统一）。

AgentRun.transcript = {"timeline": [event...], "tool_calls": [...]}。
每个 event 形如 {"t": iso时间, "kind": phase|thinking|tool|decision|answer|exec, ...}。
supervisor 跑完调 `save_run` 建行；托管结算后调 `append_run` 往同一行追加执行步骤 + 更新 outcome。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def event(kind: str, **fields) -> dict:
    """构造一个带时间戳的时间轴事件。"""
    return {"t": now_iso(), "kind": kind, **fields}


def save_run(symbol: str, mode: str, trigger: str, *, timeline: list,
             tool_calls: list, decision=None, outcome=None) -> int | None:
    try:
        from db import get_session
        from models import AgentRun
        with get_session() as db:
            row = AgentRun(
                symbol=symbol, mode=mode, trigger=trigger,
                transcript={"timeline": timeline, "tool_calls": tool_calls},
                decision=decision, outcome=outcome or {})
            db.add(row)
            db.flush()
            return row.id
    except Exception as e:                       # noqa: BLE001
        log.warning("save AgentRun %s 失败: %s", symbol, e)
        return None


def append_run(run_id: int, *, events: list | None = None,
               decision=None, outcome_patch: dict | None = None) -> None:
    """往已存在的 AgentRun 追加时间轴事件 / 合并 outcome（托管结算用）。"""
    if not run_id:
        return
    try:
        from db import get_session
        from models import AgentRun
        with get_session() as db:
            row = db.get(AgentRun, run_id)
            if not row:
                return
            tr = dict(row.transcript or {})
            tl = list(tr.get("timeline") or [])
            tl.extend(events or [])
            tr["timeline"] = tl
            row.transcript = tr
            if decision is not None:
                row.decision = decision
            if outcome_patch:
                row.outcome = {**(row.outcome or {}), **outcome_patch}
    except Exception as e:                       # noqa: BLE001
        log.warning("append AgentRun %s 失败: %s", run_id, e)
