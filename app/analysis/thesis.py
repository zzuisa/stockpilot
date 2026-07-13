"""每只股票的研究档案（thesis / 知识体系）——Agent 的反思记忆。

- `recall(symbol)`：取当前 thesis + 把「到期的下一验证点」挑出来，产出注入 prompt 的文本块。
- `distill(symbol, new_answer, ...)`：用 LLM 把「旧档案 + 本次结论」合并成新 thesis，version+1，
  写 append-only 快照（StockThesisRev），返回新 thesis。闭环：下次 recall 读到的就是进化后的。

数字纪律沿用全局：thesis 里只放定性判断与结构化要点，具体数字每次由 market_data 现取。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from db import get_session
from models import StockThesis, StockThesisRev

log = logging.getLogger(__name__)

# thesis 的目标结构（供 LLM 蒸馏时对齐；缺字段允许为空）
_SCHEMA_HINT = {
    "核心逻辑": "一段话：这只票的投资主线",
    "多空要点": {"多": ["..."], "空": ["..."]},
    "关键假设": ["支撑主线成立的前提"],
    "已验证事实": ["带日期的已确认结论"],
    "待验证问题": ["尚未确认、需后续跟踪的问题"],
    "估值锚": {"bear": "", "base": "", "bull": ""},
    "风险清单": ["..."],
    "下一验证点": [{"事件": "如 Q2 财报", "目标日期": "YYYY-MM-DD", "关注": "看什么指标"}],
}


def load_thesis(symbol: str) -> dict | None:
    sym = symbol.split("_")[0].upper()
    try:
        with get_session() as db:
            row = db.get(StockThesis, sym)
            return dict(row.thesis) if row and row.thesis else None
    except Exception as e:                       # noqa: BLE001
        log.warning("load_thesis %s 失败: %s", sym, e)
        return None


def _due_points(thesis: dict) -> list[dict]:
    """挑出目标日期已到/已过的验证点（触发『上次说等 X 验证，现在该复核了』）。"""
    today = datetime.now(timezone.utc).date()
    due = []
    for vp in (thesis.get("下一验证点") or []):
        d = vp.get("目标日期")
        try:
            if d and date.fromisoformat(str(d)[:10]) <= today:
                due.append(vp)
        except Exception:
            continue
    return due


def recall(symbol: str) -> tuple[dict | None, str]:
    """返回 (thesis, 注入文本)。无档案时文本为空串（首次分析冷启动）。"""
    thesis = load_thesis(symbol)
    if not thesis:
        return None, ""
    due = _due_points(thesis)
    parts = ["【该标的既有研究档案（在此基础上进化：确认/推翻/细化，不要从零重写）】",
             json.dumps(thesis, ensure_ascii=False)]
    if due:
        parts.append("【到期待复核的验证点（请在本次分析中给出结论：是否成立/证伪/顺延）】\n"
                     + json.dumps(due, ensure_ascii=False))
    return thesis, "\n".join(parts)


async def distill(symbol: str, new_answer: str, *, prior: dict | None = None,
                  source_run_id: int | None = None, emit=None) -> dict | None:
    """把本次分析结论蒸馏进 thesis 并落库。失败不抛（分析主流程不应因记忆写入失败而中断）。"""
    sym = symbol.split("_")[0].upper()
    if prior is None:
        prior = load_thesis(sym)
    from price_attribution_agents import _emit, _parse_json, _run_llm

    system = (
        "你是投研档案维护员。给你某标的的『既有档案(JSON)』与『本次最新分析』，"
        "请输出进化后的档案 JSON：确认仍成立的结论、用新证据推翻或细化旧结论、更新已验证/待验证、"
        "刷新下一验证点(带目标日期)。只保留定性判断与结构化要点，不要塞入会过期的具体数字。"
        f"字段结构参考：{json.dumps(_SCHEMA_HINT, ensure_ascii=False)}。"
        "再给一句 changelog 说明本次相对上版改了什么。"
        '只输出 JSON：{"thesis":{...},"changelog":"..."}。')
    human = (f"标的：{sym}\n\n既有档案：{json.dumps(prior, ensure_ascii=False) if prior else '（无，首次建立）'}"
             f"\n\n本次最新分析：\n{new_answer[:6000]}")
    try:
        raw = await _run_llm("thesis", system, human, emit, temperature=0.2)
        data = _parse_json(raw) or {}
        new_thesis = data.get("thesis")
        if not isinstance(new_thesis, dict):
            log.warning("distill %s：LLM 未产出合法 thesis，跳过", sym)
            return prior
        changelog = data.get("changelog") or ""
        _save(sym, new_thesis, changelog, source_run_id)
        await _emit(emit, {"type": "phase", "agent": "thesis", "status": "done",
                           "detail": f"研究档案已更新：{changelog[:60]}"})
        return new_thesis
    except Exception as e:                       # noqa: BLE001
        log.warning("distill %s 失败: %s", sym, e)
        return prior


def _save(symbol: str, new_thesis: dict, changelog: str,
          source_run_id: int | None) -> None:
    safe = json.loads(json.dumps(new_thesis, ensure_ascii=False, default=str))
    with get_session() as db:
        row = db.get(StockThesis, symbol)
        version = (row.version + 1) if row else 1
        if row:
            row.thesis = safe
            row.version = version
        else:
            db.add(StockThesis(symbol=symbol, thesis=safe, version=version))
        db.add(StockThesisRev(symbol=symbol, version=version, thesis=safe,
                              changelog=changelog, source_run_id=source_run_id))
