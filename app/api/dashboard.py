"""看板数据 API + 手动触发任务(便于验证,不必等调度)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select

import jobs
from db import get_db
from models import (AccountSnapshot, IndicatorDaily, JobRun, News,
                    OrderIntent, PositionSnapshot, Signal, T212CommunityPost,
                    T212Order, TradeLog)

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
    from analysis.news_quality import quality_score
    news = [{"title": n.title, "source": n.source,
             "source_name": n.source_name, "source_tier": n.source_tier,
             "relevance": n.relevance,
             "quality": quality_score(n.source_tier, n.relevance, n.published),
             "url": n.url, "score": n.sentiment, "reason": n.llm_reason}
            for n in db.execute(select(News).where(
                News.symbol == symbol, News.published >= since)
                .order_by(News.published.desc()).limit(40)).scalars()]
    # 按质量分降序，高质量新闻优先展示
    news.sort(key=lambda x: x["quality"], reverse=True)
    news = news[:20]
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
               "progress": j.progress, "detail": j.detail}
              for j in db.execute(select(JobRun)
                                  .order_by(JobRun.started_at.desc())
                                  .limit(50)).scalars()]
    return {"available": sorted(jobs.JOBS.keys()), "recent_runs": recent}


@router.get("/dashboard/updates")
async def data_updates(since: str | None = None, limit: int = 50, db=Depends(get_db)):
    """数据更新流（应用内通知中心）：新闻/情绪/信号每次更新一条，倒序。
    传 since(ISO 时间)只取其后的新更新，供前端轮询增量提示。"""
    from models import DataUpdate
    q = select(DataUpdate).order_by(DataUpdate.ts.desc()).limit(min(limit, 200))
    if since:
        try:
            ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
            q = q.where(DataUpdate.ts > ts)
        except ValueError:
            pass
    return [{"id": u.id, "ts": u.ts, "kind": u.kind, "symbol": u.symbol,
             "title": u.title, "detail": u.detail}
            for u in db.execute(q).scalars()]


@router.post("/jobs/{name}/run")
async def trigger_job(name: str, background_tasks: BackgroundTasks):
    """手动触发调度任务(立即返回,后台执行)"""
    if name not in jobs.JOBS:
        raise HTTPException(404, f"未知任务 {name},可用: "
                            + ", ".join(sorted(jobs.JOBS)))
    background_tasks.add_task(jobs.run_job, name)
    return {"job": name, "status": "started"}


# ════════════ 交易历史 + 概览大屏数据 ════════════

@router.get("/trades")
async def list_trades(limit: int = 100, source: str | None = None,
                      symbol: str | None = None, db=Depends(get_db)):
    """统一交易历史(手动 + 量化),倒序。"""
    q = select(TradeLog).order_by(TradeLog.ts.desc()).limit(min(limit, 500))
    if source:
        q = q.where(TradeLog.source == source)
    if symbol:
        q = q.where(TradeLog.symbol == symbol.upper())
    return [{"ts": t.ts, "source": t.source, "symbol": t.symbol,
             "t212_ticker": t.t212_ticker, "side": t.side,
             "order_type": t.order_type, "quantity": t.quantity,
             "price": t.price, "value_eur": t.value_eur, "pnl": t.pnl,
             "reason": t.reason, "status": t.status, "order_id": t.order_id,
             "env": t.env} for t in db.execute(q).scalars()]


@router.delete("/trades")
async def clear_trades(db=Depends(get_db)):
    """清空交易历史(trade_log)：一键重置近期成交 / 交易统计 / 今日盈亏。"""
    n = db.query(TradeLog).delete()
    return {"deleted": int(n or 0)}


@router.get("/dashboard/equity-curve")
async def equity_curve(days: int = 30, db=Depends(get_db)):
    """账户净值时间序列(用于资产曲线)。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(AccountSnapshot).where(AccountSnapshot.ts >= since)
        .order_by(AccountSnapshot.ts)
    ).scalars().all()
    return [{"ts": r.ts, "total": r.total, "free_cash": r.free_cash,
             "invested": r.invested, "ppl": r.ppl, "result": r.result}
            for r in rows]


def _active_account_id() -> int | None:
    try:
        from t212.account_cache import get_active
        a = get_active()
        return a["id"] if a else None
    except Exception:
        return None


def _real_fills(db, account_id: int | None) -> list[dict]:
    """以 T212 真实成交(T212Order, status=FILLED)按时间升序，逐 ticker 用均价法
    计算每笔卖出的已实现盈亏(账户币种, 含 FX/费, 取自 walletImpact.netValue)。
    返回每笔成交 dict, 时间升序; 买入 pnl=None, 卖出 pnl=已实现。"""
    q = select(T212Order).where(T212Order.status == "FILLED")
    if account_id is not None:
        q = q.where(T212Order.account_id == account_id)
    q = q.order_by(T212Order.filled_at.asc().nullslast(), T212Order.id.asc())
    rows = db.execute(q).scalars().all()

    book: dict[str, list[float]] = {}   # ticker -> [qty, cost]（账户币种）
    fills: list[dict] = []
    for r in rows:
        tk = r.ticker or ""
        qty = abs(float(r.filled_quantity or 0))
        val = abs(float(r.filled_value or 0))         # 账户币种净额
        side = (r.side or "").upper()
        pnl = None
        st = book.setdefault(tk, [0.0, 0.0])
        if side == "BUY":
            st[0] += qty
            st[1] += val
        elif side == "SELL" and st[0] > 1e-9:
            avg = st[1] / st[0]
            sold = min(qty, st[0])
            cost = avg * sold
            pnl = round(val - cost, 2)                # 已实现盈亏
            st[0] -= sold
            st[1] -= cost
            if st[0] < 1e-9:
                st[0], st[1] = 0.0, 0.0
        fills.append({
            "ts": r.filled_at or r.date_created,
            "ticker": tk,
            "symbol": tk.split("_")[0] if tk else None,
            "side": side.lower(),
            "quantity": qty,
            "price": float(r.fill_price or 0),
            "value_eur": round(val, 2),
            "pnl": pnl,
            "order_type": (r.type or "").lower(),
            "order_id": str(r.id),
        })
    return fills


@router.get("/dashboard/recent-fills")
async def recent_fills(limit: int = 8, db=Depends(get_db)):
    """近期【真实成交】(来自 T212 订单历史, 非挂单/估算)。附策略原因(按 order_id 关联)。"""
    fills = [f for f in _real_fills(db, _active_account_id()) if f["ts"]]
    fills.sort(key=lambda f: f["ts"], reverse=True)
    fills = fills[:min(limit, 100)]
    # 关联策略原因：trade_log.order_id ↔ T212Order.id
    ids = [f["order_id"] for f in fills]
    reason_map: dict[str, str] = {}
    if ids:
        for t in db.execute(
            select(TradeLog.order_id, TradeLog.reason)
            .where(TradeLog.order_id.in_(ids))
        ).all():
            if t.order_id and t.reason:
                reason_map[str(t.order_id)] = t.reason
    for f in fills:
        f["reason"] = reason_map.get(f["order_id"])
    return fills


@router.get("/dashboard/trade-stats")
async def trade_stats(days: int = 30, db=Depends(get_db)):
    """成交统计(真实成交)：胜率 / 已实现盈亏 / 按日盈亏 / 成交笔数。
    已实现盈亏按 T212 真实成交价 + 账户币种净额, 均价法配对买卖计算。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    fills = _real_fills(db, _active_account_id())
    in_window = [f for f in fills if f["ts"] and f["ts"] >= since]
    # 已实现 = 窗口内有 pnl 的卖出
    closed = [f for f in in_window if f["side"] == "sell" and f["pnl"] is not None]
    wins = sum(1 for f in closed if f["pnl"] > 0)
    losses = sum(1 for f in closed if f["pnl"] < 0)
    realized = round(sum(f["pnl"] for f in closed), 2)
    by_day: dict[str, float] = {}
    for f in closed:
        day = f["ts"].astimezone(timezone.utc).date().isoformat()
        by_day[day] = round(by_day.get(day, 0) + f["pnl"], 2)
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "trade_count": len(closed),                   # 平仓(卖出)笔数=胜率基数
        "win": wins, "loss": losses,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else 0.0,
        "realized_pnl": realized,
        "today_pnl": by_day.get(today, 0.0),
        "total_fills": len(in_window),                # 窗口内全部真实成交笔数
        "by_day": [{"day": d, "pnl": v} for d, v in sorted(by_day.items())],
    }
