"""管理 API(说明书 §12):Group / Symbol / Recipient CRUD + 路由查询 +
推送日志 + YAML 同步导出。不写前端,直接 curl。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select

import config
from db import get_db, get_session
from models import Group, NotifyLog, NotifyRoute, WatchlistItem

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["watchlist"])


def _rebuild_group_routes(db, group: Group):
    """按 group.config + watchlist 重新展开 notify_routes"""
    db.query(NotifyRoute).filter(NotifyRoute.group_id == group.id).delete()
    items = db.query(WatchlistItem).filter(
        WatchlistItem.group_id == group.id, WatchlistItem.active).all()
    gdict = {**(group.config or {}), "id": group.id,
             "symbols": [{"ticker": w.symbol,
                          "t212_ticker": w.t212_ticker,
                          **(w.symbol_config or {})} for w in items]}
    for row in config.expand_routes({}, gdict):
        db.add(NotifyRoute(**row))


# ─── Group CRUD ───

class GroupCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    notify_channels: list[str] = ["telegram"]
    telegram_chat_ids: list[str] = []
    email_recipients: list[str] = []
    notify_on: list[str] = ["daily_report", "signal"]


@router.get("/groups")
async def list_groups(db=Depends(get_db)):
    groups = db.execute(select(Group).order_by(Group.id)).scalars().all()
    result = []
    for g in groups:
        cnt = db.execute(
            select(func.count()).select_from(WatchlistItem).where(
                WatchlistItem.group_id == g.id, WatchlistItem.active.is_(True)
            )
        ).scalar()
        result.append({"id": g.id, "name": g.name, "description": g.description,
                       "config": g.config, "symbol_count": cnt})
    return result


@router.get("/groups/{group_id}")
async def get_group(group_id: str, db=Depends(get_db)):
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    items = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.group_id == group_id,
            WatchlistItem.active.is_(True)
        )
    ).scalars().all()
    return {
        "id": grp.id, "name": grp.name, "description": grp.description,
        "config": grp.config,
        "symbols": [
            {"symbol": w.symbol, "t212_ticker": w.t212_ticker,
             "tags": w.tags or [], "symbol_config": w.symbol_config or {}}
            for w in items
        ],
    }


@router.post("/groups", status_code=201)
async def create_group(g: GroupCreate, db=Depends(get_db)):
    if db.get(Group, g.id):
        raise HTTPException(409, f"group {g.id} 已存在")
    grp = Group(id=g.id, name=g.name, description=g.description,
                config=g.model_dump(exclude={"id", "name", "description"}))
    db.add(grp)
    db.flush()
    _rebuild_group_routes(db, grp)
    return {"ok": True, "id": g.id}


@router.put("/groups/{group_id}")
async def update_group(group_id: str, g: GroupCreate, db=Depends(get_db)):
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    grp.name = g.name
    grp.description = g.description
    grp.config = {**(grp.config or {}),
                  **g.model_dump(exclude={"id", "name", "description"})}
    _rebuild_group_routes(db, grp)
    return {"ok": True}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, db=Depends(get_db)):
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    db.query(NotifyRoute).filter(NotifyRoute.group_id == group_id).delete()
    db.query(WatchlistItem).filter(
        WatchlistItem.group_id == group_id).delete()
    db.delete(grp)
    return {"ok": True}


# ─── Symbol CRUD ───

class SymbolAdd(BaseModel):
    symbol: str
    t212_ticker: str | None = None
    tags: list[str] = []
    extra_email: list[str] = []
    extra_telegram: list[str] = []
    community_priority: str | None = None  # positive | negative | all
    news_auto: bool | None = None          # 是否自动拉取该股新闻 + 生成精华
    news_sources: list[str] | None = None  # finnhub | alphavantage
    news_types: list[str] | None = None    # earnings/announcement/... (见 config.NEWS_TYPES)


@router.post("/groups/{group_id}/symbols", status_code=201)
async def add_symbol(group_id: str, s: SymbolAdd,
                     background_tasks: BackgroundTasks, db=Depends(get_db)):
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    sym = s.symbol.upper()
    item = db.get(WatchlistItem, (sym, group_id))
    scfg = {k: v for k, v in s.model_dump().items()
            if k not in ("symbol", "t212_ticker", "tags") and v}
    if item:                       # 幂等:已存在则合并更新
        item.t212_ticker = s.t212_ticker or item.t212_ticker
        item.tags = s.tags or item.tags
        item.symbol_config = {**(item.symbol_config or {}), **scfg}
        item.active = True
    else:
        db.add(WatchlistItem(symbol=sym, group_id=group_id,
                             t212_ticker=s.t212_ticker, tags=s.tags,
                             symbol_config=scfg, active=True))
    db.flush()
    _rebuild_group_routes(db, grp)
    # 加入自选后即时补全数据(日线+指标/分钟线/新闻/社区/精华)，后台执行不阻塞响应
    import jobs
    background_tasks.add_task(jobs.job_ensure_symbol, sym)
    return {"ok": True, "symbol": sym, "group_id": group_id, "ensuring": True}


class SymbolNewsConfig(BaseModel):
    news_auto: bool = False
    news_sources: list[str] = ["finnhub", "alphavantage"]
    news_types: list[str] = []             # 空 = 全部类别


@router.put("/groups/{group_id}/symbols/{symbol}/news")
async def update_symbol_news(group_id: str, symbol: str,
                             body: SymbolNewsConfig, db=Depends(get_db)):
    """更新单只股票的新闻自动拉取配置(开关/来源/类别),写入 symbol_config。"""
    item = db.get(WatchlistItem, (symbol.upper(), group_id))
    if not item:
        raise HTTPException(404, "symbol 不在该 group")
    sources = [s for s in body.news_sources if s in config.NEWS_SOURCES]
    types = [t for t in body.news_types if t in config.NEWS_TYPES]
    item.symbol_config = {
        **(item.symbol_config or {}),
        "news_auto": bool(body.news_auto),
        "news_sources": sources or list(config.NEWS_SOURCES),
        "news_types": types,    # 空表示全部,由 config 解析时回退
    }
    db.flush()
    return {"ok": True, "symbol": symbol.upper(),
            "news_auto": bool(body.news_auto)}


@router.delete("/groups/{group_id}/symbols/{symbol}")
async def remove_symbol(group_id: str, symbol: str, db=Depends(get_db)):
    item = db.get(WatchlistItem, (symbol.upper(), group_id))
    if not item:
        raise HTTPException(404, "symbol 不在该 group")
    db.delete(item)
    db.flush()
    grp = db.get(Group, group_id)
    if grp:
        _rebuild_group_routes(db, grp)
    return {"ok": True}


# ─── Recipient 管理 ───

class RecipientUpdate(BaseModel):
    channel: str                      # 'telegram' | 'email'
    recipients: list[str]
    event_types: list[str] = ["daily_report", "signal"]


class NotifyRecipient(BaseModel):
    channel: str                      # 'telegram' | 'email'
    recipient: str
    events: list[str] = ["daily_report", "signal"]


class NotifyUpdate(BaseModel):
    recipients: list[NotifyRecipient] = []


@router.put("/groups/{group_id}/notify")
async def update_notify(group_id: str, body: NotifyUpdate, db=Depends(get_db)):
    """按接收人逐个配置推送(矩阵)。写入 config.recipients 并重建路由。"""
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    recips = []
    for r in body.recipients:
        if r.channel not in ("telegram", "email"):
            raise HTTPException(422, "channel 必须是 telegram | email")
        if not r.recipient.strip():
            continue
        recips.append({"channel": r.channel, "recipient": r.recipient.strip(),
                       "events": r.events})
    cfg = dict(grp.config or {})
    cfg["recipients"] = recips
    # 同步维护旧字段,便于回退与 YAML 导出
    cfg["telegram_chat_ids"] = [r["recipient"] for r in recips if r["channel"] == "telegram"]
    cfg["email_recipients"] = [r["recipient"] for r in recips if r["channel"] == "email"]
    cfg["notify_channels"] = sorted({r["channel"] for r in recips})
    grp.config = cfg
    _rebuild_group_routes(db, grp)
    return {"ok": True, "count": len(recips)}


@router.put("/groups/{group_id}/recipients")
async def update_recipients(group_id: str, r: RecipientUpdate,
                            db=Depends(get_db)):
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "group 不存在")
    if r.channel not in ("telegram", "email"):
        raise HTTPException(422, "channel 必须是 telegram | email")
    cfg = dict(grp.config or {})
    key = "telegram_chat_ids" if r.channel == "telegram" else "email_recipients"
    cfg[key] = r.recipients
    channels = set(cfg.get("notify_channels") or [])
    channels.add(r.channel)
    cfg["notify_channels"] = sorted(channels)
    cfg["notify_on"] = r.event_types
    grp.config = cfg
    _rebuild_group_routes(db, grp)
    return {"ok": True}


# ─── 查询 ───

@router.get("/routes")
async def list_routes(group_id: str | None = None,
                      symbol: str | None = None, db=Depends(get_db)):
    q = select(NotifyRoute).where(NotifyRoute.active.is_(True))
    if group_id:
        q = q.where(NotifyRoute.group_id == group_id)
    if symbol:
        q = q.where(NotifyRoute.symbol == symbol.upper())
    return [{"id": r.id, "group_id": r.group_id, "symbol": r.symbol,
             "channel": r.channel, "recipient": r.recipient,
             "event_types": r.event_types}
            for r in db.execute(q.order_by(NotifyRoute.group_id)).scalars()]


@router.get("/notify-log")
async def get_notify_log(hours: int = 24, db=Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(select(NotifyLog).where(NotifyLog.ts >= since)
                      .order_by(NotifyLog.ts.desc()).limit(500)).scalars()
    return [{"ts": r.ts, "event_type": r.event_type, "group_id": r.group_id,
             "symbol": r.symbol, "channel": r.channel,
             "recipient": r.recipient, "status": r.status,
             "error_msg": r.error_msg} for r in rows]


# ─── 配置同步 ───

@router.post("/sync-yaml")
async def sync_from_yaml(db=Depends(get_db)):
    """重新从 watchlist.yaml 加载,覆盖数据库(幂等)"""
    return config.sync_to_db(db)


@router.get("/export-yaml", response_class=PlainTextResponse)
async def export_to_yaml(db=Depends(get_db)):
    """把数据库当前状态导出为 YAML"""
    return config.export_yaml(db)


# ─── 分组即时推送 ───

@router.post("/groups/{group_id}/push")
async def push_group_report(group_id: str, background_tasks: BackgroundTasks,
                            db=Depends(get_db)):
    """手动触发指定分组的即时报告推送（Telegram/Email）"""
    grp = db.get(Group, group_id)
    if not grp:
        raise HTTPException(404, "分组不存在")
    background_tasks.add_task(_push_group_bg, group_id)
    return {"ok": True, "group_id": group_id}


async def _push_group_bg(group_id: str):
    from analysis import report
    from notify import get_router

    def _build():
        with get_session() as s:
            grp = s.get(Group, group_id)
            if not grp:
                return []
            payload = report.render_group_report(s, grp)
            return [{"event_type": "daily_report", "group_id": grp.id, "payload": payload}]

    try:
        events = await asyncio.to_thread(_build)
        if events:
            await get_router().dispatch_events(events)
        log.info("push_group %s done (%d events)", group_id, len(events))
    except Exception as e:
        log.warning("push_group %s failed: %s", group_id, e)
