"""新闻 LLM 专项：全局新闻分析结果流 + 统计（供 News LLM 页签）。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from db import get_db
from models import News, NewsBrief

router = APIRouter(prefix="/api/v1/news", tags=["news_llm"])


def _quality(tier, relevance, published):
    try:
        from analysis.news_quality import quality_score
        return quality_score(tier, relevance, published)
    except Exception:
        return None


@router.get("")
def list_news(
    limit: int = Query(80, le=300),
    symbol: str | None = None,
    source: str | None = None,
    scored: str | None = None,        # '1'=仅已分析, '0'=仅未分析, None=全部
    db=Depends(get_db),
):
    """最新新闻 + LLM 分析结果，按发布时间倒序。"""
    order_ts = func.coalesce(News.published, News.fetched_at)
    q = select(News).order_by(order_ts.desc()).limit(limit)
    if symbol:
        q = q.where(News.symbol == symbol.upper())
    if source:
        q = q.where(News.source == source)
    if scored == "1":
        q = q.where(News.sentiment.isnot(None))
    elif scored == "0":
        q = q.where(News.sentiment.is_(None))
    rows = db.execute(q).scalars().all()
    return [{
        "id": n.id,
        "symbol": n.symbol,
        "source": n.source,
        "source_name": n.source_name,
        "source_tier": n.source_tier,
        "relevance": n.relevance,
        "quality": _quality(n.source_tier, n.relevance, n.published),
        "title": n.title,
        "url": n.url,
        "published": n.published,
        "fetched_at": n.fetched_at,
        "sentiment": n.sentiment,
        "llm_reason": n.llm_reason,
    } for n in rows]


@router.get("/briefs")
def list_briefs(
    limit: int = Query(40, le=200),
    symbol: str | None = None,
    db=Depends(get_db),
):
    """最新新闻精华(LLM 高信号筛选 + 投资判断),按生成时间倒序。"""
    q = select(NewsBrief).order_by(NewsBrief.ts.desc()).limit(limit)
    if symbol:
        q = q.where(NewsBrief.symbol == symbol.upper())
    rows = db.execute(q).scalars().all()
    return [{
        "id": b.id,
        "symbol": b.symbol,
        "ts": b.ts,
        "window_hours": b.window_hours,
        "headline": b.headline,
        "sentiment": b.sentiment,
        "judgment": b.judgment,
        "summary_md": b.summary_md,
        "watch_points": b.watch_points,
        "item_count": b.item_count,
        "tokens": b.tokens,
        "pushed": b.pushed,
    } for b in rows]


@router.get("/llm-status")
def llm_status():
    """常驻 LLM 工作器实时状态：运行/进度/累计token/最近一轮等。"""
    from analysis.sentiment import LLM_STATUS
    return dict(LLM_STATUS)


@router.get("/stats")
def news_stats(hours: int = Query(72, le=720), db=Depends(get_db)):
    """近 N 小时新闻分析统计：总数 / 已分析 / 未分析 / 情绪分布 / 均分。"""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = select(News).where(func.coalesce(News.published, News.fetched_at) >= since)

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    scored = db.execute(
        select(func.count()).select_from(News)
        .where(func.coalesce(News.published, News.fetched_at) >= since,
               News.sentiment.isnot(None))
    ).scalar() or 0

    dist = {str(k): 0 for k in (-2, -1, 0, 1, 2)}
    rows = db.execute(
        select(News.sentiment, func.count())
        .where(func.coalesce(News.published, News.fetched_at) >= since,
               News.sentiment.isnot(None))
        .group_by(News.sentiment)
    ).all()
    for sent, cnt in rows:
        if sent is not None and str(sent) in dist:
            dist[str(sent)] = int(cnt)

    avg = db.execute(
        select(func.avg(News.sentiment))
        .where(func.coalesce(News.published, News.fetched_at) >= since,
               News.sentiment.isnot(None))
    ).scalar()
    last_scored = db.execute(
        select(func.max(News.fetched_at)).where(News.sentiment.isnot(None))
    ).scalar()

    return {
        "hours": hours,
        "total": int(total),
        "scored": int(scored),
        "unscored": int(total) - int(scored),
        "by_sentiment": dist,
        "avg": round(float(avg), 3) if avg is not None else None,
        "last_scored_ts": last_scored,
    }
