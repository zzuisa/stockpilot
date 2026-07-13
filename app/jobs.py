"""调度任务(说明书 §20 调度表 + §14 数据流)。
所有任务可经 POST /api/v1/jobs/{name}/run 手动触发,运行记录写 job_runs。
"""
import asyncio
import contextvars
import json
import logging
from datetime import datetime, timedelta, timezone

import config
from db import get_session
from models import DataUpdate, JobRun

log = logging.getLogger(__name__)

# 当前运行的 JobRun id（asyncio.to_thread 会复制 context，线程内也可读）
_current_run: contextvars.ContextVar = contextvars.ContextVar("run_id", default=None)


def progress(text: str) -> None:
    """更新当前任务的实时进度（任务页可见）。无当前任务时静默忽略。"""
    rid = _current_run.get()
    if not rid:
        return
    try:
        with get_session() as s:
            jr = s.get(JobRun, rid)
            if jr:
                jr.progress = (text or "")[:300]
    except Exception as e:
        log.debug("progress update failed: %s", e)


def record_update(db, kind: str, symbol: str | None, title: str,
                  detail: dict | None = None) -> None:
    """写一条数据更新流（应用内通知中心 + Telegram 推送来源）。"""
    db.add(DataUpdate(kind=kind, symbol=symbol, title=title[:300], detail=detail))


def _record_start(name: str) -> int:
    with get_session() as s:
        jr = JobRun(job_name=name)
        s.add(jr)
        s.flush()
        return jr.id


def _record_end(run_id: int, status: str, detail):
    with get_session() as s:
        jr = s.get(JobRun, run_id)
        if jr:
            jr.status = status
            jr.finished_at = datetime.now(timezone.utc)
            jr.detail = (json.dumps(detail, ensure_ascii=False, default=str)
                         if not isinstance(detail, str) else detail)[:2000]


async def _run(name: str, fn) -> dict:
    run_id = _record_start(name)
    token = _current_run.set(run_id)
    try:
        result = await fn()
        _record_end(run_id, "ok", result)
        return {"job": name, "status": "ok", "result": result}
    except Exception as e:
        log.exception("job %s failed", name)
        _record_end(run_id, "failed", str(e))
        return {"job": name, "status": "failed", "error": str(e)}
    finally:
        _current_run.reset(token)


def _symbols():
    with get_session() as s:
        return [x["symbol"] for x in config.active_symbols(s)]


def _yf_map() -> dict[str, str]:
    """symbol → yf_symbol 映射(有 yf_symbol 覆盖才会不同,例如德股 2DG→2DG.SG)"""
    with get_session() as s:
        return {x["symbol"]: x["yf_symbol"] for x in config.active_symbols(s)}


# ─── 各任务实现 ───

async def job_t212_sync():
    def work():
        from t212 import sync
        with get_session() as s:
            snap = sync.sync_account_snapshot(s)
            try:
                hist = sync.sync_history(s)  # 订单/分红历史增量（真实成交来源）
            except Exception as e:
                log.warning("sync_history 失败: %s", e)
                hist = {"error": str(e)}
            return {**snap, "history": hist}
    return await _run("t212_sync", lambda: asyncio.to_thread(work))


async def job_intraday():
    """盘中:watchlist 分钟线 + Finnhub 快讯"""
    def work():
        from collectors import news, prices
        syms = _symbols()
        yf_map = _yf_map()
        with get_session() as s:
            r1 = prices.fetch_intraday(syms, s, yf_map=yf_map)
            # 盘中快讯只为开启 news_auto 且选了 finnhub 来源的标的拉取(不再无差别全量)
            news_syms = [d["symbol"] for d in config.news_symbols(s)
                         if "finnhub" in d["news_sources"]]
            r2 = news.fetch_finnhub(news_syms, s, days=1) if news_syms else 0
        return {"prices": r1, "finnhub_news": r2}
    return await _run("intraday", lambda: asyncio.to_thread(work))


async def job_daily_prices():
    """22:40 日线采集 + 指标计算;顺带 T212 订单/分红历史增量"""
    def work():
        from analysis import indicators
        from collectors import prices
        from t212 import sync
        syms = _symbols()
        yf_map = _yf_map()
        with get_session() as s:
            r1 = prices.fetch_daily(syms, s, yf_map=yf_map)
            r2 = indicators.compute_all(s)
            r3 = sync.sync_history(s)
        return {"prices": r1, "indicators": r2, "t212_history": r3}
    return await _run("daily_prices", lambda: asyncio.to_thread(work))


async def job_community():
    """22:50 ★ T212 社区帖子采集"""
    def work():
        from t212 import community
        with get_session() as s:
            return community.collect_for_watchlist(s)
    return await _run("community", lambda: asyncio.to_thread(work))


async def job_news():
    """新闻全量(finnhub + rss + alphavantage)，记录每标的新增为更新流"""
    start = datetime.now(timezone.utc)

    def work():
        from sqlalchemy import func, select
        from collectors import news
        from models import News
        with get_session() as s:
            progress("采集新闻 finnhub/rss/alphavantage…")
            r = news.collect_all(s)
            s.flush()
            rows = s.execute(
                select(News.symbol, func.count(), func.min(News.source_tier))
                .where(News.fetched_at >= start, News.symbol.isnot(None))
                .group_by(News.symbol)).all()
            for sym, cnt, best_tier in rows:
                tag = " · 含权威源" if best_tier == 1 else (
                    " · 含主流源" if best_tier == 2 else "")
                record_update(s, "news", sym, f"{sym}: {cnt} 条新新闻{tag}",
                              {"count": int(cnt), "best_tier": best_tier})
            return {**r, "symbols_updated": len(rows)}
    res = await _run("news", lambda: asyncio.to_thread(work))
    # 采集后立即生成并推送「精华总结 + 投资判断」(不再推原始标题)
    await job_news_brief()
    return res


def _brief_events_for_symbol(sc: dict) -> list[dict]:
    """为单只股票生成精华并落库，返回 news_shock 推送事件(无实质内容返回[])。
    供 job_news_brief(全量) 与 job_ensure_symbol(单标的) 复用。"""
    from analysis import news_brief
    from models import NewsBrief
    sym = sc["symbol"]
    with get_session() as s:
        brief = news_brief.build_symbol_brief(s, sc)
        if not brief or brief["item_count"] == 0:
            return []
        body_md = brief["body_md"]
        payload = {
            "subject": f"{sym} 新闻精华 · {brief['sentiment']}",
            "body_md": body_md,
            "body_html": body_md.replace("\n", "<br>"),
        }
        record_update(
            s, "news_brief", sym,
            f"{sym}: 新闻精华 {brief['item_count']} 条 · {brief['sentiment']}",
            {"id": brief["id"], "sentiment": brief["sentiment"]})
        s.flush()
        events = [{"event_type": "news_shock", "symbol": sym,
                   "group_id": gid, "payload": payload}
                  for gid in brief["groups"]]
    with get_session() as s2:
        nb = s2.get(NewsBrief, brief["id"])
        if nb:
            nb.pushed = True
    return events


async def job_news_brief():
    """逐只开启 news_auto 的股票生成新闻精华(LLM 高信号筛选 + 投资判断),
    按其所属分组以 news_shock 事件推送(复用 notify_routes + 24h 防重发)。
    只推送有实质内容的股票。"""

    def _build() -> list[dict]:
        events: list[dict] = []
        with get_session() as s:
            syms = config.news_symbols(s)
        for i, sc in enumerate(syms, 1):
            progress(f"生成新闻精华 {sc['symbol']} ({i}/{len(syms)})")
            try:
                events.extend(_brief_events_for_symbol(sc))
            except Exception as e:
                log.warning("news_brief %s 失败: %s", sc["symbol"], e)
        return events

    async def work():
        from notify import get_router
        events = await asyncio.to_thread(_build)
        if events:
            await get_router().dispatch_events(events)
        return {"briefs": len({e["symbol"] for e in events}),
                "events": len(events)}
    return await _run("news_brief", work)


def _collect_community_symbol(sym: str) -> int:
    """为单只股票即时采集 T212 社区帖(复用 collect_for_watchlist 的入库逻辑)。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from t212.community import T212Community, _parse_ts
    from models import T212CommunityPost
    cli = T212Community()
    total = 0
    with get_session() as db:
        for p in cli.search_symbol(sym):
            stmt = pg_insert(T212CommunityPost).values(
                topic_id=p["topic_id"], post_id=p["post_id"], symbol=sym,
                author=p["author"], content=p["content"],
                published=_parse_ts(p["published"]), likes=p["likes"] or 0,
            ).on_conflict_do_nothing(index_elements=["post_id"])
            total += db.execute(stmt).rowcount or 0
    return total


async def job_ensure_symbol(symbol: str):
    """新标的加入自选后即时补全数据：日线(1y 回填)+技术指标、当日分钟线，
    并按该标的有效配置补新闻(news_auto)与社区帖(t212_community)，开启新闻时生成并推送精华。
    使新标的立刻具备图表 / 指标 / 情绪 / 建议所需数据，不必等夜间调度。"""
    sym = symbol.upper()

    def _fill():
        from analysis import indicators
        from collectors import news, prices
        out: dict = {}
        # 1) 日线 + 技术指标(缺则回填 1 年)
        try:
            ens = indicators.ensure_symbol(sym)
            out["prices_indicators"] = "ready" if ens.get("ready") else "partial"
        except Exception as e:
            log.warning("ensure_symbol prices/indicators %s 失败: %s", sym, e)
            out["prices_indicators"] = "failed"
        # 解析该标的有效配置(新闻/社区/来源/yf 映射)
        sc = None
        with get_session() as s:
            for d in config.active_symbols(s):
                if d["symbol"] == sym:
                    sc = dict(d)
                    break
        if not sc:
            return out, None
        yf_map = {sym: sc.get("yf_symbol") or sym}
        # 2) 当日分钟线(实时/详情用)
        try:
            with get_session() as s:
                out["intraday"] = prices.fetch_intraday([sym], s, yf_map=yf_map)
        except Exception as e:
            log.warning("ensure_symbol intraday %s 失败: %s", sym, e)
        # 3) 新闻(仅 news_auto，按配置来源；非美股追加富途资讯)
        if sc.get("news_auto"):
            try:
                with get_session() as s:
                    nn = 0
                    if "finnhub" in sc["news_sources"]:
                        nn += news.fetch_finnhub([sym], s)
                    if "alphavantage" in sc["news_sources"]:
                        nn += news.fetch_alphavantage([sym], s)
                    if config.market_of(sc) != "US" or "futu" in sc["news_sources"]:
                        nn += news.fetch_futu(sc, s)
                    out["news"] = nn
            except Exception as e:
                log.warning("ensure_symbol news %s 失败: %s", sym, e)
        # 4) 社区帖(仅 t212_community)
        if sc.get("t212_community"):
            try:
                out["community"] = _collect_community_symbol(sym)
            except Exception as e:
                log.warning("ensure_symbol community %s 失败: %s", sym, e)
        return out, sc

    async def work():
        out, sc = await asyncio.to_thread(_fill)
        # 5) 开启新闻的标的：生成并推送精华
        if sc and sc.get("news_auto"):
            try:
                events = await asyncio.to_thread(_brief_events_for_symbol, sc)
                if events:
                    from notify import get_router
                    await get_router().dispatch_events(events)
                    out["brief"] = "pushed"
            except Exception as e:
                log.warning("ensure_symbol brief %s 失败: %s", sym, e)
        with get_session() as s:
            record_update(s, "data", sym, f"{sym}: 新标的数据已补全", out)
        return out

    return await _run("ensure_symbol", work)


async def job_sentiment():
    """情绪批处理(新闻 + 社区统一 LLM 打分 + 解读)，记录每标的情绪更新"""
    start = datetime.now(timezone.utc)

    def work():
        from sqlalchemy import func, select
        from analysis import sentiment
        from models import News
        progress("LLM 情绪打分 + 解读中…")
        r = sentiment.run_sentiment_batch(on_progress=progress)
        # 记录每标的情绪更新到更新流(独立短会话)
        with get_session() as s:
            rows = s.execute(
                select(News.symbol, func.count(), func.avg(News.sentiment))
                .where(News.symbol.isnot(None),
                       News.fetched_at >= start - timedelta(days=3),
                       News.sentiment.isnot(None))
                .group_by(News.symbol)).all()
            for sym, cnt, avg in rows:
                if not cnt:
                    continue
                record_update(s, "sentiment", sym,
                              f"{sym}: 情绪更新 均分 {float(avg or 0):+.1f} ({cnt} 条)",
                              {"count": int(cnt), "avg": round(float(avg or 0), 2)})
        return r
    return await _run("sentiment", lambda: asyncio.to_thread(work))


async def job_signals():
    """信号评估 + 即时推送(signal / news_shock)，记录信号更新流"""
    start = datetime.now(timezone.utc)

    async def work():
        from notify import get_router
        events = await asyncio.to_thread(_eval)
        await get_router().dispatch_events(events)
        return {"events": len(events)}

    def _eval():
        from analysis import signals
        with get_session() as s:
            progress("评估技术/情绪信号…")
            events = signals.evaluate(s)
            for e in events:
                sym = e.get("symbol")
                etype = e.get("event_type", "signal")
                rule = (e.get("payload") or {}).get("rule") or etype
                record_update(s, "signal", sym, f"{sym}: 新信号 {rule}",
                              {"event_type": etype, "rule": rule})
            return events
    res = await _run("signals", work)
    await _push_digest(["signal"], start)
    return res


async def _push_digest(kinds: list[str], since) -> None:
    """把本轮新增的数据更新汇总推送到主 Telegram chat（实时知道哪些股票更新了什么）。"""
    import settings
    if not (settings.telegram_enabled and settings.TELEGRAM_CHAT_ID):
        return

    def _q():
        from sqlalchemy import select
        with get_session() as s:
            return [u.title for u in s.execute(
                select(DataUpdate).where(DataUpdate.ts >= since,
                                         DataUpdate.kind.in_(kinds))
                .order_by(DataUpdate.ts)).scalars()]
    try:
        titles = await asyncio.to_thread(_q)
        if not titles:
            return
        from notify.telegram import TelegramSender
        txt = "🔔 <b>数据更新</b>\n" + "\n".join("· " + t for t in titles[:15])
        if len(titles) > 15:
            txt += f"\n… 共 {len(titles)} 条"
        await TelegramSender().send(settings.TELEGRAM_CHAT_ID, txt)
    except Exception as e:
        log.warning("digest push failed: %s", e)


async def job_daily_report():
    """08:00 按 group 逐组生成早报 + 分发"""
    async def work():
        from notify import get_router
        events = await asyncio.to_thread(_build)
        await get_router().dispatch_events(events)
        return {"groups": len(events)}

    def _build():
        from analysis import report
        with get_session() as s:
            return report.build_all_reports(s)
    return await _run("daily_report", work)


async def job_expire_intents():
    def work():
        from trading import executor
        with get_session() as s:
            return {"expired": executor.expire_stale(s)}
    # 高频小任务不写 job_runs,避免刷屏
    try:
        return await asyncio.to_thread(work)
    except Exception as e:
        log.warning("expire_intents failed: %s", e)
        return {"error": str(e)}


async def job_ensure_indicators():
    """自愈补全技术指标(每 30min)：检查 watchlist + 在跑量化标的，指标缺失/过期者
    自动(必要时回填 1 年日线后)补算 IndicatorDaily。已有新鲜指标的标的廉价跳过，
    避免依赖每天一次的 daily_prices；新加入的标的(如量化新标的)数分钟内即有指标。"""
    def work():
        from datetime import date, timedelta
        from sqlalchemy import func, select
        from analysis import indicators
        from collectors import prices
        from models import IndicatorDaily, Price, QuantStrategy
        with get_session() as s:
            # 范围：watchlist 自选 ∪ 正在运行的量化标的
            targets: dict[str, str] = {
                x["symbol"]: x["yf_symbol"] for x in config.active_symbols(s)
            }
            for q in s.query(QuantStrategy).filter(QuantStrategy.active).all():
                targets.setdefault(q.symbol, q.symbol)

            stale_cut = date.today() - timedelta(days=4)   # 指标超 4 天视为过期
            computed, backfilled, skipped, failed = [], [], 0, []
            for sym, yf_sym in targets.items():
                try:
                    last = s.execute(select(func.max(IndicatorDaily.ts))
                                     .where(IndicatorDaily.symbol == sym)).scalar()
                    if last and last >= stale_cut:
                        skipped += 1
                        continue
                    # 日线不足则回填 1 年(带 yf_map，正确处理德股等映射)
                    npx = s.execute(select(func.count()).select_from(Price)
                                    .where(Price.symbol == sym,
                                           Price.interval == "1d")).scalar() or 0
                    if npx < 210:
                        prices.fetch_daily([sym], s, period="1y",
                                           yf_map={sym: yf_sym})
                        backfilled.append(sym)
                    if indicators.compute_symbol(s, sym):
                        computed.append(sym)
                    else:
                        failed.append(sym)
                except Exception as e:
                    log.warning("ensure_indicators %s 失败: %s", sym, e)
                    failed.append(sym)
            return {"computed": computed, "backfilled": backfilled,
                    "skipped": skipped, "failed": failed}
    return await _run("ensure_indicators", lambda: asyncio.to_thread(work))


async def job_backfill():
    """首次部署:2 年日线回填(§8)"""
    def work():
        from collectors import prices
        syms = _symbols()
        with get_session() as s:
            if not prices.needs_backfill(s):
                return {"skipped": "already backfilled"}
            return prices.backfill(syms, s)
    return await _run("backfill", lambda: asyncio.to_thread(work))


async def job_autonomy():
    """全 Agent 托管循环：对开启托管的标的自主分析→反思→决策→预算内执行。
    仅当全局托管开启且非 kill-switch 时有动作（内部再逐标的判定）。"""
    async def work():
        from agents.autonomy import run_all
        return await run_all()
    return await _run("autonomy", work)


JOBS = {
    "t212_sync": job_t212_sync,
    "intraday": job_intraday,
    "daily_prices": job_daily_prices,
    "community": job_community,
    "news": job_news,
    "news_brief": job_news_brief,
    "sentiment": job_sentiment,
    "signals": job_signals,
    "daily_report": job_daily_report,
    "ensure_indicators": job_ensure_indicators,
    "backfill": job_backfill,
    "autonomy": job_autonomy,
}


async def run_job(name: str) -> dict:
    if name not in JOBS:
        raise KeyError(name)
    return await JOBS[name]()
