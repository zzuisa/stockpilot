"""看板数据 API + 手动触发任务(便于验证,不必等调度)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select

import jobs
from db import get_db
from models import (AccountSnapshot, IndicatorDaily, JobRun, News,
                    OrderIntent, PositionSnapshot, Signal, T212CommunityPost)

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard/summary")
async def summary(db=Depends(get_db)):
    acc = db.execute(select(AccountSnapshot)
                     .order_by(AccountSnapshot.ts.desc()).limit(1)
                     ).scalar_one_or_none()
    latest_ts = db.execute(select(func.max(PositionSnapshot.ts))).scalar()
    positions = []
    if latest_ts:
        positions = [
            {"ticker": p.ticker, "quantity": p.quantity,
             "avg_price": p.average_price, "current_price": p.current_price,
             "ppl": p.ppl}
            for p in db.execute(select(PositionSnapshot).where(
                PositionSnapshot.ts == latest_ts)).scalars()]
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    signals = [{"ts": s.ts, "symbol": s.symbol, "rule": s.rule,
                "direction": s.direction, "strength": s.strength}
               for s in db.execute(select(Signal).where(Signal.ts >= since)
                                   .order_by(Signal.ts.desc())).scalars()]
    pending = [{"id": i.id, "symbol": i.symbol, "side": i.side,
                "value": i.order_value_eur, "expires_at": i.expires_at}
               for i in db.execute(select(OrderIntent).where(
                   OrderIntent.status == "pending")).scalars()]
    return {"account": {"ts": acc.ts, "total": acc.total,
                        "free_cash": acc.free_cash, "ppl": acc.ppl}
            if acc else None,
            "positions": positions, "signals_24h": signals,
            "pending_intents": pending}


@router.get("/dashboard/sentiment/{symbol}")
async def sentiment_detail(symbol: str, days: int = 3, db=Depends(get_db)):
    from analysis import sentiment as sa
    symbol = symbol.upper()
    agg = sa.symbol_aggregates(db, symbol, days)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    news = [{"title": n.title, "source": n.source, "url": n.url,
             "score": n.sentiment, "reason": n.llm_reason}
            for n in db.execute(select(News).where(
                News.symbol == symbol, News.published >= since)
                .order_by(News.published.desc()).limit(20)).scalars()]
    posts = [{"post_id": p.post_id, "author": p.author, "likes": p.likes,
              "score": p.sentiment, "content": (p.content or "")[:200]}
             for p in db.execute(select(T212CommunityPost).where(
                 T212CommunityPost.symbol == symbol,
                 T212CommunityPost.published >= since)
                 .order_by(T212CommunityPost.likes.desc()).limit(20)
             ).scalars()]
    return {"symbol": symbol, "aggregates": agg,
            "label": sa.community_signal_label(agg["comm_avg"],
                                               agg["comm_cnt"]),
            "news": news, "community": posts}


@router.get("/dashboard/indicators/{symbol}")
async def indicators_detail(symbol: str, db=Depends(get_db)):
    row = db.execute(select(IndicatorDaily)
                     .where(IndicatorDaily.symbol == symbol.upper())
                     .order_by(IndicatorDaily.ts.desc()).limit(1)
                     ).scalar_one_or_none()
    if not row:
        return None
    return {c.name: getattr(row, c.name)
            for c in IndicatorDaily.__table__.columns}


@router.get("/intents")
async def list_intents(status: str | None = None, db=Depends(get_db)):
    q = select(OrderIntent).order_by(OrderIntent.created_at.desc()).limit(100)
    if status:
        q = q.where(OrderIntent.status == status)
    return [{"id": i.id, "created_at": i.created_at, "symbol": i.symbol,
             "side": i.side, "rule": i.rule, "value": i.order_value_eur,
             "quantity": i.quantity, "status": i.status,
             "status_reason": i.status_reason} for i in
            db.execute(q).scalars()]


@router.get("/jobs")
async def list_jobs(db=Depends(get_db)):
    recent = [{"job": j.job_name, "started_at": j.started_at,
               "finished_at": j.finished_at, "status": j.status,
               "detail": j.detail}
              for j in db.execute(select(JobRun)
                                  .order_by(JobRun.started_at.desc())
                                  .limit(50)).scalars()]
    return {"available": sorted(jobs.JOBS.keys()), "recent_runs": recent}


@router.post("/jobs/{name}/run")
async def trigger_job(name: str, background_tasks: BackgroundTasks):
    """手动触发调度任务(立即返回,后台执行)"""
    if name not in jobs.JOBS:
        raise HTTPException(404, f"未知任务 {name},可用: "
                            + ", ".join(sorted(jobs.JOBS)))
    background_tasks.add_task(jobs.run_job, name)
    return {"job": name, "status": "started"}
