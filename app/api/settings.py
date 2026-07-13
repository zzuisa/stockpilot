"""应用设置端点：全 Agent 托管开关、风险预算、kill-switch、MCP 写暴露。

此前系统完全没有设置表/端点——所有旋钮都在 env。这里给出 DB 支撑、UI 可编辑的全局设置，
供托管循环与写工具的确认策略消费。
"""
import logging

from fastapi import APIRouter, Body

from analysis import appsettings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("")
async def get_settings(symbol: str | None = None):
    """symbol 省略→全局；给了→全局+该标的覆盖（生效值）。"""
    cfg = appsettings.get_for(symbol) if symbol else appsettings.get_global()
    return {"scope": symbol.upper() if symbol else "global", "settings": cfg,
            "defaults": appsettings.DEFAULTS}


@router.put("")
async def put_settings(patch: dict = Body(...), symbol: str | None = None):
    """浅合并写入（risk_budget 深合并）。symbol 给了则写按标的覆盖。"""
    key = f"sym:{symbol.upper()}" if symbol else "global"
    merged = appsettings.put(key, patch or {})
    log.info("settings updated key=%s patch=%s", key, list((patch or {}).keys()))
    return {"ok": True, "scope": key, "settings": merged}
