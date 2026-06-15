"""StockPilot 入口:FastAPI + APScheduler(说明书 §3 main.py)

调度表(§20,欧洲时间):
  每 30 min            T212 持仓/现金快照
  盘中每 15 min        watchlist 分钟线 + Finnhub 快讯 (Mon-Fri 15:30–22:00)
  22:40                日线采集 + 指标计算
  22:50              ★ T212 社区帖子采集
  23:00                Dify 情绪批处理(新闻 + 社区统一)
  23:10                信号评估 + 即时推送
  08:00              ★ 按 group 逐组生成早报 + 分发
  (周日 03:00 pg_dump 由 k8s CronJob 承担,见 k8s/backup-cronjob.yaml)
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import text

import config
import db as database
import jobs
import settings
import quant
from api import dashboard, stream, t212_market, watchlist, webhook
from api import quant as quant_api
from notify import telegram as tg

_STATIC = Path(__file__).parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stockpilot")

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)


def _schedule_jobs():
    add = scheduler.add_job
    # T212 快照:每 30 分钟
    add(jobs.job_t212_sync, CronTrigger(minute="*/30"), id="t212_sync")
    # 盘中分钟线 + 快讯:Mon–Fri 15:30–22:00 每 15 分钟
    add(jobs.job_intraday, CronTrigger(day_of_week="mon-fri", hour="15",
                                       minute="30,45"), id="intraday_open")
    add(jobs.job_intraday, CronTrigger(day_of_week="mon-fri", hour="16-21",
                                       minute="*/15"), id="intraday")
    add(jobs.job_intraday, CronTrigger(day_of_week="mon-fri", hour="22",
                                       minute="0"), id="intraday_close")
    # 收盘后流水线
    add(jobs.job_daily_prices, CronTrigger(day_of_week="mon-fri", hour=22,
                                           minute=40), id="daily_prices")
    add(jobs.job_community, CronTrigger(day_of_week="mon-fri", hour=22,
                                        minute=50), id="community")
    add(jobs.job_news, CronTrigger(day_of_week="mon-fri", hour=22,
                                   minute=55), id="news")
    add(jobs.job_sentiment, CronTrigger(day_of_week="mon-fri", hour=23,
                                        minute=0), id="sentiment")
    add(jobs.job_signals, CronTrigger(day_of_week="mon-fri", hour=23,
                                      minute=10), id="signals")
    # 次日早报
    add(jobs.job_daily_report, CronTrigger(day_of_week="mon-fri", hour=8,
                                           minute=0), id="daily_report")
    # 过期 intent 清理
    add(jobs.job_expire_intents, CronTrigger(minute="*/5"),
        id="expire_intents")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # tsdb 可能在同批启动,带重试等待
    for attempt in range(60):
        try:
            database.init_db()
            break
        except Exception as e:
            log.warning("等待数据库... (%d/60) %s", attempt + 1, e)
            await asyncio.sleep(5)
    else:
        raise RuntimeError("数据库不可用")

    with database.get_session() as s:
        from sqlalchemy import text as _text
        existing = s.execute(_text("SELECT COUNT(*) FROM groups")).scalar() or 0
        if existing == 0:
            log.info("数据库为空,从 watchlist.yaml 初始化")
            config.sync_to_db(s)
        else:
            log.info("数据库已有 %d 个分组,跳过 YAML 覆盖(手动同步: POST /api/v1/sync-yaml)", existing)

    _schedule_jobs()
    scheduler.start()
    await tg.start_bot()

    if settings.t212_enabled:
        n = quant.resume_active()
        if n:
            log.info("量化策略已恢复: %d 个", n)

    if settings.AUTO_BACKFILL:
        async def _maybe_backfill():
            await asyncio.sleep(30)
            await jobs.job_backfill()
        asyncio.get_event_loop().create_task(_maybe_backfill())

    log.info("StockPilot 启动完成 (t212=%s finnhub=%s llm=%s tg=%s email=%s)",
             settings.t212_enabled, settings.finnhub_enabled,
             settings.llm_enabled, settings.telegram_enabled,
             settings.email_enabled)
    yield
    quant.shutdown()
    await tg.stop_bot()
    scheduler.shutdown(wait=False)


app = FastAPI(title="StockPilot", version="2.0",
              root_path=settings.ROOT_PATH, lifespan=lifespan)
app.include_router(watchlist.router)
app.include_router(dashboard.router)
app.include_router(stream.router)
app.include_router(quant_api.router)
app.include_router(t212_market.router)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    db_ok = True
    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "scheduler": scheduler.running,
            "integrations": {
                "t212": settings.t212_enabled,
                "finnhub": settings.finnhub_enabled,
                "llm": settings.llm_enabled,
                "telegram": settings.telegram_enabled,
                "email": settings.email_enabled,
            }}


@app.get("/api/v1/jobs/schedule")
async def job_schedule():
    """返回 APScheduler 当前调度列表及下次运行时间"""
    result = []
    for job in scheduler.get_jobs():
        result.append({
            "id": job.id,
            "next_run": job.next_run_time,
            "trigger": str(job.trigger),
        })
    return {"jobs": result, "timezone": settings.TIMEZONE}


@app.get("/manage", include_in_schema=False)
async def manage_redirect():
    # 相对跳转到带尾斜杠的目录，使 SPA 相对资源路径在直连端口
    # 与 nginx 子路径两种部署下都能正确解析（无需感知前缀）。
    return RedirectResponse(url="manage/")


@app.get("/")
async def root():
    return {"app": "StockPilot", "docs": f"{settings.ROOT_PATH}/docs",
            "health": f"{settings.ROOT_PATH}/health",
            "manage": f"{settings.ROOT_PATH}/manage/"}


# Vue SPA（构建产物由 Dockerfile 注入到 static/spa）。
# 用显式 FileResponse 路由而非 app.mount——Starlette 的 Mount 匹配会受
# root_path=/stockpilot 干扰；普通 APIRoute 与现有 /api 路由一致地按
# 去前缀后的 path 匹配，在直连端口与 nginx 子路径下都正确。
_SPA = _STATIC / "spa"


@app.get("/manage/", include_in_schema=False)
@app.get("/manage/{asset_path:path}", include_in_schema=False)
async def serve_spa(asset_path: str = ""):
    index = _SPA / "index.html"
    if not index.exists():
        legacy = _STATIC / "manage.html"
        if legacy.exists():
            return HTMLResponse(legacy.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>前端未构建</h1>", status_code=503)
    if asset_path:
        target = (_SPA / asset_path).resolve()
        # 防目录穿越：必须落在 _SPA 内且是真实文件
        if str(target).startswith(str(_SPA.resolve())) and target.is_file():
            return FileResponse(target)
    # 入口或未命中的子路径（hash 路由）一律回 index.html
    return FileResponse(index)
