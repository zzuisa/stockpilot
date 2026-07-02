"""活跃 T212 账户的内存缓存，避免每次请求都查 DB。
账户切换时调用 invalidate() 使缓存失效。
"""
import time
import logging

log = logging.getLogger(__name__)

_cache: dict = {"acct": None, "ts": 0.0}
_TTL = 5.0  # 5 秒 TTL，切换账户后至多 5s 生效（主动失效时立即生效）


def invalidate() -> None:
    _cache["ts"] = 0.0


def get_active() -> dict | None:
    """返回 {id, name, api_key, env} 或 None（未配置账户）"""
    now = time.monotonic()
    if now - _cache["ts"] < _TTL:
        return _cache["acct"]
    _refresh()
    return _cache["acct"]


def _refresh() -> None:
    try:
        from db import get_session
        with get_session() as s:
            from models import T212Account
            row = (s.query(T212Account)
                   .filter(T212Account.is_active.is_(True))
                   .first())
            if row:
                _cache["acct"] = {
                    "id": row.id,
                    "name": row.name,
                    "api_key": row.api_key,
                    "api_secret": row.api_secret,   # 可能为 None
                    "env": row.env,
                }
            else:
                _cache["acct"] = None
    except Exception as e:
        log.warning("account_cache refresh error: %s", e)
    _cache["ts"] = time.monotonic()


def get_client():
    """返回使用激活账户 API key 的 T212 客户端；无账户时回退到 settings。"""
    acct = get_active()
    from t212.client import T212
    if acct:
        return T212(api_key=acct["api_key"], api_secret=acct.get("api_secret"), env=acct["env"])
    import settings
    if settings.t212_enabled:
        return T212()
    from fastapi import HTTPException
    raise HTTPException(503, "未配置 T212 账户，请先在「账户」页面添加 API Key")
