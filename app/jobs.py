"""调度任务(说明书 §20 调度表 + §14 数据流)。
所有任务可经 POST /api/v1/jobs/{name}/run 手动触发,运行记录写 job_runs。
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import config
from db import get_session
from models import JobRun

log = logging.getLogger(__name__)


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
    try:
        result = await fn()
        _record_end(run_id, "ok", result)
        return {"job": name, "status": "ok", "result": result}
    except Exception as e:
        log.exception("job %s failed", name)
        _record_end(run_id, "failed", str(e))
        return {"job": name, "status": "failed", "error": str(e)}


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
            return sync.sync_account_snapshot(s)
    return await _run("t212_sync", lambda: asyncio.to_thread(work))


async def job_intraday():
    """盘中:watchlist 分钟线 + Finnhub 快讯"""
    def work():
        from collectors import news, prices
        syms = _symbols()
        yf_map = _yf_map()
        with get_session() as s:
            r1 = prices.fetch_intraday(syms, s, yf_map=yf_map)
            r2 = news.fetch_finnhub(syms, s, days=1)
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
    """新闻全量(finnhub + rss)"""
    def work():
        from collectors import news
        with get_session() as s:
            return news.collect_all(s)
    return await _run("news", lambda: asyncio.to_thread(work))


async def job_sentiment():
    """23:00 Dify 情绪批处理(新闻 + 社区统一)"""
    def work():
        from analysis import sentiment
        with get_session() as s:
            return sentiment.run_sentiment_batch(s)
    return await _run("sentiment", lambda: asyncio.to_thread(work))


async def job_signals():
    """23:10 信号评估 + 即时推送(signal / news_shock)"""
    async def work():
        from analysis import signals
        from notify import get_router
        events = await asyncio.to_thread(_eval)
        await get_router().dispatch_events(events)
        return {"events": len(events)}

    def _eval():
        from analysis import signals
        with get_session() as s:
            return signals.evaluate(s)
    return await _run("signals", work)


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


JOBS = {
    "t212_sync": job_t212_sync,
    "intraday": job_intraday,
    "daily_prices": job_daily_prices,
    "community": job_community,
    "news": job_news,
    "sentiment": job_sentiment,
    "signals": job_signals,
    "daily_report": job_daily_report,
    "backfill": job_backfill,
}


async def run_job(name: str) -> dict:
    if name not in JOBS:
        raise KeyError(name)
    return await JOBS[name]()
