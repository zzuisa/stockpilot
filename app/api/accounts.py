"""T212 账户管理 API — 多账户切换"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from db import get_db
from models import T212Account
from t212.account_cache import invalidate as invalidate_cache

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    name: str
    api_key: str
    api_secret: str | None = None
    env: str = "demo"


class AccountUpdate(BaseModel):
    name: str | None = None
    api_key: str | None = None
    api_secret: str | None = None   # "" 表示清除 secret
    env: str | None = None


def _fmt(row: T212Account) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "env": row.env,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "api_key_hint": (row.api_key[:8] + "…") if row.api_key else "",
        "has_secret": bool(row.api_secret),
    }


@router.get("")
def list_accounts(db=Depends(get_db)):
    """列出所有 T212 账户（API Key 只显示前 8 位）"""
    rows = db.execute(select(T212Account).order_by(T212Account.id)).scalars().all()
    return [_fmt(r) for r in rows]


@router.post("")
def create_account(body: AccountCreate, db=Depends(get_db)):
    """新增账户；若当前无账户，自动激活。"""
    if body.env not in ("demo", "live"):
        raise HTTPException(400, "env 须为 demo 或 live")
    existing = db.execute(select(T212Account)).scalars().all()
    is_first = len(existing) == 0
    acct = T212Account(name=body.name, api_key=body.api_key,
                       api_secret=body.api_secret or None,
                       env=body.env, is_active=is_first)
    db.add(acct)
    db.flush()
    if is_first:
        invalidate_cache()
    log.info("T212 账户已创建: %s (env=%s, active=%s)", acct.name, acct.env, acct.is_active)
    return _fmt(acct)


@router.put("/{acct_id}")
def update_account(acct_id: int, body: AccountUpdate, db=Depends(get_db)):
    """更新账户名/API Key/env。"""
    acct = db.get(T212Account, acct_id)
    if not acct:
        raise HTTPException(404, "账户不存在")
    if body.name is not None:
        acct.name = body.name
    if body.api_key is not None:
        acct.api_key = body.api_key
    if body.api_secret is not None:
        acct.api_secret = body.api_secret or None  # "" → 清除
    if body.env is not None:
        if body.env not in ("demo", "live"):
            raise HTTPException(400, "env 须为 demo 或 live")
        acct.env = body.env
    db.flush()
    if acct.is_active:
        invalidate_cache()
    return _fmt(acct)


@router.delete("/{acct_id}")
def delete_account(acct_id: int, db=Depends(get_db)):
    """删除账户（不能删除当前激活账户）。"""
    acct = db.get(T212Account, acct_id)
    if not acct:
        raise HTTPException(404, "账户不存在")
    if acct.is_active:
        raise HTTPException(400, "不能删除当前激活的账户，请先切换到其他账户")
    db.delete(acct)
    return {"ok": True, "deleted": acct_id}


@router.post("/{acct_id}/activate")
def activate_account(acct_id: int, db=Depends(get_db)):
    """切换激活账户（同时停止当前账户的所有量化策略运行）。"""
    acct = db.get(T212Account, acct_id)
    if not acct:
        raise HTTPException(404, "账户不存在")
    # 取消激活其他账户
    for row in db.execute(select(T212Account)).scalars().all():
        row.is_active = (row.id == acct_id)
    db.flush()
    invalidate_cache()
    log.info("T212 账户切换: %s (env=%s)", acct.name, acct.env)
    return {"ok": True, "active_id": acct_id, "name": acct.name, "env": acct.env}
