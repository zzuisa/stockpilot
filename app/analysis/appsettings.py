"""全局应用设置服务（此前完全没有设置存储）。

单表 app_settings，key='global' 存主设置，key='sym:<SYM>' 存按标的覆盖。
消费方：托管循环(autonomy)、写工具的确认策略、MCP 写暴露开关、risk 预算。
读多写少 → 简单读库，不做缓存（设置项极少）。
"""
from __future__ import annotations

import logging

import settings as env
from db import get_session
from models import AppSetting

log = logging.getLogger(__name__)

DEFAULTS = {
    "autonomy_enabled": False,       # 全 Agent 托管总开关
    "kill_switch": False,            # 一键停：置 True 立即禁止一切自动执行
    "mcp_expose_write": False,       # 是否把写类工具也经 MCP 暴露给外部 Agent（默认否）
    "risk_budget": {
        "max_order_eur": env.RISK_MAX_ORDER_EUR,        # 单笔金额上限（建 intent 用）
        "auto_execute_max_eur": 50.0,                   # 托管“自动执行”的单笔上限，超过→升级人工确认
        "daily_auto_trades": 3,                         # 托管每日自动成交笔数上限
    },
}


def _row(key: str) -> dict:
    try:
        with get_session() as db:
            r = db.get(AppSetting, key)
            return dict(r.value) if r and r.value else {}
    except Exception as e:                       # noqa: BLE001
        log.warning("appsettings 读取 %s 失败: %s", key, e)
        return {}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def get_global() -> dict:
    return _merge(DEFAULTS, _row("global"))


def get_for(symbol: str) -> dict:
    """全局 + 该标的覆盖。"""
    sym = symbol.split("_")[0].upper()
    return _merge(get_global(), _row(f"sym:{sym}"))


def put(key: str, patch: dict) -> dict:
    """合并写入（浅合并 + risk_budget 深合并）。返回写后的完整值。"""
    with get_session() as db:
        r = db.get(AppSetting, key)
        cur = dict(r.value) if r and r.value else {}
        merged = _merge(cur, patch)
        if r:
            r.value = merged
        else:
            db.add(AppSetting(key=key, value=merged))
    return merged


# ── 便捷判定（消费方直接用）───────────────────────────────────────────────────────
def autonomy_enabled(symbol: str | None = None) -> bool:
    cfg = get_for(symbol) if symbol else get_global()
    return bool(cfg.get("autonomy_enabled")) and not bool(cfg.get("kill_switch"))


def kill_switch() -> bool:
    return bool(get_global().get("kill_switch"))


def mcp_expose_write() -> bool:
    return bool(get_global().get("mcp_expose_write"))


def auto_execute_cap(symbol: str | None = None) -> float:
    cfg = get_for(symbol) if symbol else get_global()
    return float((cfg.get("risk_budget") or {}).get("auto_execute_max_eur", 0) or 0)
