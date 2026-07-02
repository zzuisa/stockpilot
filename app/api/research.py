"""财报 + LLM 泡沫分析 + 投资策略 API"""
import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse as _FileResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from db import get_db, get_session
from models import EarningsReport, IndicatorDaily, InvestmentAnalysis

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/research", tags=["research"])

REPORT_DIR = Path("/appHome/application/StockPilot/report")


# ─── 财报 ─────────────────────────────────────────────────────────────────────

@router.get("/reports/{symbol}")
async def list_reports(symbol: str, db=Depends(get_db)):
    """列出该标的已下载的财报"""
    sym = symbol.upper()
    rows = db.execute(
        select(EarningsReport).where(EarningsReport.symbol == sym)
        .order_by(EarningsReport.downloaded_at.desc())
    ).scalars().all()
    return [
        {"id": r.id, "symbol": r.symbol, "period": r.period,
         "downloaded_at": r.downloaded_at, "filename": r.filename,
         "size": r.size, "source": r.source}
        for r in rows
    ]


class DownloadRequest(BaseModel):
    period: str | None = None


@router.post("/reports/{symbol}/download")
async def download_report(symbol: str, body: DownloadRequest, db=Depends(get_db)):
    """下载并存储指定股票的季度财报（yfinance）"""
    sym = symbol.upper()
    try:
        from analysis.research import download_quarterly_report
        result = await asyncio.to_thread(download_quarterly_report, sym, body.period)
    except Exception as e:
        raise HTTPException(400, f"财报下载失败: {e}")

    existing = db.execute(
        select(EarningsReport).where(
            EarningsReport.symbol == sym,
            EarningsReport.period == result["period"],
        ).limit(1)
    ).scalars().first()

    if existing:
        existing.filename = result["filename"]
        existing.path = result["path"]
        existing.size = result["size"]
        from models import utcnow
        existing.downloaded_at = utcnow()
    else:
        db.add(EarningsReport(
            symbol=sym, period=result["period"],
            filename=result["filename"], path=result["path"],
            size=result["size"], source="yfinance",
        ))

    return {"symbol": sym, "period": result["period"],
            "filename": result["filename"], "size": result["size"], "ok": True}


@router.get("/reports/{symbol}/{filename}")
async def serve_report(symbol: str, filename: str):
    """下载/查看财报原文"""
    sym = symbol.upper()
    safe = REPORT_DIR.resolve()
    path = (REPORT_DIR / sym / filename).resolve()
    if not str(path).startswith(str(safe)) or not path.exists():
        raise HTTPException(404, "文件不存在")
    return _FileResponse(path, media_type="text/plain; charset=utf-8",
                         filename=filename)


# ─── LLM 分析 ─────────────────────────────────────────────────────────────────

def _get_indicators(db, sym: str) -> tuple[dict, list]:
    """返回 (latest_indicators_dict, history_list[最近10条])"""
    rows = db.execute(
        select(IndicatorDaily).where(IndicatorDaily.symbol == sym)
        .order_by(IndicatorDaily.ts.desc()).limit(10)
    ).scalars().all()
    if not rows:
        return {}, []
    latest = rows[0]
    ind = {
        "rsi": latest.rsi, "macd": latest.macd,
        "macd_signal": latest.macd_signal, "macd_hist": latest.macd_hist,
        "macd_cross": latest.macd_cross, "sma20": latest.sma20,
        "sma50": latest.sma50, "sma200": latest.sma200,
        "atr": latest.atr, "bb_upper": latest.bb_upper,
        "bb_lower": latest.bb_lower, "vol_ratio": latest.vol_ratio,
        "close": latest.close,
    }
    history = [
        {"ts": str(r.ts), "rsi": r.rsi, "macd": r.macd,
         "macd_hist": r.macd_hist, "close": r.close}
        for r in reversed(rows)
    ]
    return ind, history


@router.post("/analyze/{symbol}/bubble")
async def analyze_bubble(symbol: str, db=Depends(get_db)):
    """LLM 泡沫分析（需先下载财报）"""
    sym = symbol.upper()

    report_row = db.execute(
        select(EarningsReport).where(EarningsReport.symbol == sym)
        .order_by(EarningsReport.downloaded_at.desc()).limit(1)
    ).scalars().first()
    if not report_row:
        raise HTTPException(404, "请先下载财报")

    report_path = Path(report_row.path)
    if not report_path.exists():
        raise HTTPException(404, "财报文件不存在，请重新下载")
    report_text = report_path.read_text(encoding="utf-8")

    indicators, _ = _get_indicators(db, sym)
    if not indicators:
        from analysis.indicators import ensure_symbol
        await asyncio.to_thread(ensure_symbol, sym)
        indicators, _ = _get_indicators(db, sym)

    from analysis.sentiment import symbol_aggregates
    with get_session() as s:
        news_agg = symbol_aggregates(s, sym, days=7)

    current_price = indicators.get("close") or 0.0

    from analysis.research import llm_bubble_analysis
    result, tokens = await asyncio.to_thread(
        llm_bubble_analysis, sym, report_text, news_agg, current_price, indicators
    )
    if not result:
        raise HTTPException(503, "LLM 未启用或分析失败，请检查 SILICONFLOW_API_KEY")

    db.add(InvestmentAnalysis(
        symbol=sym, analysis_type="bubble",
        bubble_level=result.get("bubble_level"),
        bubble_pct=result.get("bubble_pct"),
        strategy_text=json.dumps(result, ensure_ascii=False),
        tokens=tokens,
        key_metrics={"indicators": indicators, "news_agg": news_agg,
                     "current_price": current_price},
        report_period=report_row.period,
    ))

    return {**result, "tokens": tokens, "report_period": report_row.period}


@router.post("/brief/{symbol}")
async def daily_brief(symbol: str, db=Depends(get_db)):
    """生成单股盘前日报(今日结论/趋势证据/期权结论/观察条件)。缺数据自动补全。"""
    from analysis.brief import build_daily_brief
    sym = symbol.upper()
    yf_symbol = None
    from models import WatchlistItem
    w = db.query(WatchlistItem).filter(WatchlistItem.symbol == sym).first()
    if w:
        yf_symbol = (w.symbol_config or {}).get("yf_symbol")
    res = await asyncio.to_thread(build_daily_brief, sym, yf_symbol)
    if not res.get("ok"):
        raise HTTPException(422, res.get("error", "日报生成失败"))
    db.add(InvestmentAnalysis(
        symbol=sym, analysis_type="brief",
        strategy_text=json.dumps(res, ensure_ascii=False),
        key_metrics={"price": res.get("price"), "change_pct": res.get("change_pct")},
    ))
    try:
        from jobs import record_update
        with get_session() as s:
            record_update(s, "brief", sym,
                          f"{sym}: 盘前日报已生成 · {res.get('change_pct')}%")
    except Exception as e:
        log.warning("brief update log failed: %s", e)
    return res


@router.post("/brief/{symbol}/comment")
async def daily_brief_comment(symbol: str, db=Depends(get_db)):
    """为最近一次盘前日报异步生成 LLM 解读(四段文案)并回填。与主请求解耦，
    避免慢 LLM 拖垮 brief 触发超时。"""
    sym = symbol.upper()
    row = db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.symbol == sym,
            InvestmentAnalysis.analysis_type == "brief",
        ).order_by(InvestmentAnalysis.ts.desc()).limit(1)
    ).scalars().first()
    if not row or not row.strategy_text:
        raise HTTPException(404, "请先生成盘前日报")
    data = json.loads(row.strategy_text)
    facts = data.get("facts")
    if not facts:
        raise HTTPException(422, "日报缺少分析输入，请重新生成")
    from analysis.brief import _llm_comment
    comment = await asyncio.to_thread(_llm_comment, facts)
    if comment:
        data["comment"] = comment
        row.strategy_text = json.dumps(data, ensure_ascii=False)
    return comment


@router.get("/brief/{symbol}/latest")
async def daily_brief_latest(symbol: str, db=Depends(get_db)):
    """最近一次盘前日报(无则 204 空)。"""
    row = db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.symbol == symbol.upper(),
            InvestmentAnalysis.analysis_type == "brief",
        ).order_by(InvestmentAnalysis.ts.desc()).limit(1)
    ).scalars().first()
    if not row or not row.strategy_text:
        return {}
    try:
        return json.loads(row.strategy_text)
    except Exception:
        return {}


@router.get("/options/{symbol}")
async def options_metrics(symbol: str):
    """期权综合指标(GEX/PCR/IV/Put-Call Wall/Gamma by Strike)。无期权返回 404。"""
    from analysis.options import option_metrics
    sym = symbol.upper()
    yf_symbol = None
    with get_session() as s:
        from models import WatchlistItem
        w = s.query(WatchlistItem).filter(WatchlistItem.symbol == sym).first()
        if w:
            yf_symbol = (w.symbol_config or {}).get("yf_symbol")
    res = await asyncio.to_thread(option_metrics, sym, yf_symbol)
    if not res:
        raise HTTPException(404, f"{sym} 无期权数据(非美股期权标的或 yfinance 暂不可用)")
    return res


@router.post("/ensure-data/{symbol}")
async def ensure_data(symbol: str):
    """按需补全该标的的日线 + 技术指标(研究分析的数据前置)。
    返回分步结果供前端流程可视化；成功后写入更新流(通知中心可见)。"""
    from analysis.indicators import ensure_symbol
    res = await asyncio.to_thread(ensure_symbol, symbol)
    try:
        from jobs import record_update
        with get_session() as s:
            if res.get("ready"):
                record_update(s, "data", symbol.upper(),
                              f"{symbol.upper()}: 已按需补全日线+技术指标")
            else:
                bad = next((st for st in res.get("steps", [])
                            if st["status"] == "failed"), None)
                record_update(s, "data", symbol.upper(),
                              f"{symbol.upper()}: 数据补全失败 · {bad['detail'] if bad else ''}")
    except Exception as e:
        log.warning("ensure_data update log failed: %s", e)
    return res


@router.post("/analyze/{symbol}/strategy")
async def analyze_strategy(symbol: str, db=Depends(get_db)):
    """一键 LLM 深度投资策略分析（缺指标时自动按需补全）"""
    sym = symbol.upper()

    indicators, history = _get_indicators(db, sym)
    if not indicators:
        # 自愈：自动补全日线+指标后重试
        from analysis.indicators import ensure_symbol
        res = await asyncio.to_thread(ensure_symbol, sym)
        indicators, history = _get_indicators(db, sym)
        if not indicators:
            bad = next((st for st in res.get("steps", [])
                        if st["status"] == "failed"), None)
            raise HTTPException(
                422, f"无法获取 {sym} 的日线数据自动补全失败"
                + (f"：{bad['detail']}" if bad else "，请确认代码/交易所后缀"))

    from analysis.sentiment import symbol_aggregates
    with get_session() as s:
        news_agg = symbol_aggregates(s, sym, days=7)

    bubble_row = db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.symbol == sym,
            InvestmentAnalysis.analysis_type == "bubble",
        ).order_by(InvestmentAnalysis.ts.desc()).limit(1)
    ).scalars().first()

    bubble: dict = {}
    if bubble_row and bubble_row.strategy_text:
        try:
            bubble = json.loads(bubble_row.strategy_text)
        except Exception:
            pass

    current_price = indicators.get("close") or 0.0

    from analysis.research import llm_investment_strategy
    result, tokens = await asyncio.to_thread(
        llm_investment_strategy, sym, indicators, history,
        news_agg, bubble, current_price
    )
    if not result:
        raise HTTPException(503, "LLM 未启用或分析失败，请检查 SILICONFLOW_API_KEY")

    db.add(InvestmentAnalysis(
        symbol=sym, analysis_type="strategy",
        bubble_level=bubble.get("bubble_level"),
        bubble_pct=bubble.get("bubble_pct"),
        strategy_text=json.dumps(result, ensure_ascii=False),
        tokens=tokens,
        key_metrics={"indicators": indicators, "news_agg": news_agg,
                     "current_price": current_price, "bubble": bubble},
    ))

    return {**result, "tokens": tokens, "current_price": current_price}


@router.get("/analyze/{symbol}/latest")
async def get_latest_analysis(symbol: str, db=Depends(get_db)):
    """获取最新泡沫分析 + 最新投资策略（含历史指标趋势数据）"""
    sym = symbol.upper()

    bubble_row = db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.symbol == sym,
            InvestmentAnalysis.analysis_type == "bubble",
        ).order_by(InvestmentAnalysis.ts.desc()).limit(1)
    ).scalars().first()

    strategy_row = db.execute(
        select(InvestmentAnalysis).where(
            InvestmentAnalysis.symbol == sym,
            InvestmentAnalysis.analysis_type == "strategy",
        ).order_by(InvestmentAnalysis.ts.desc()).limit(1)
    ).scalars().first()

    # 历史指标（折线图用）
    ind_rows = db.execute(
        select(IndicatorDaily).where(IndicatorDaily.symbol == sym)
        .order_by(IndicatorDaily.ts.desc()).limit(30)
    ).scalars().all()
    ind_history = [
        {"ts": str(r.ts), "close": r.close, "rsi": r.rsi,
         "macd": r.macd, "macd_signal": r.macd_signal, "macd_hist": r.macd_hist}
        for r in reversed(ind_rows)
    ]

    result: dict = {"history": ind_history}
    if bubble_row:
        result["bubble"] = {
            "ts": bubble_row.ts,
            "bubble_level": bubble_row.bubble_level,
            "bubble_pct": bubble_row.bubble_pct,
            "tokens": bubble_row.tokens,
            "report_period": bubble_row.report_period,
            "data": json.loads(bubble_row.strategy_text) if bubble_row.strategy_text else {},
        }
    if strategy_row:
        result["strategy"] = {
            "ts": strategy_row.ts,
            "tokens": strategy_row.tokens,
            "data": json.loads(strategy_row.strategy_text) if strategy_row.strategy_text else {},
        }
    return result


# ─── 前瞻短线建议（SSE 流式，融合盘前日报多维信息）─────────────────────────────
@router.get("/advice/stream")
async def advice_stream(
    request: Request,
    symbol: str = Query(...),
    start: str | None = Query(None, description="ISO8601 起始，可空=最近窗口"),
    end: str | None = Query(None, description="ISO8601 结束，可空=至今"),
):
    """右侧常备面板 / 归因面板复用：流式产出当前走向 + 短线建议（多维理论支撑）。
    套用 api/attribution.py 的 queue + StreamingResponse 写法。"""
    from analysis import advice

    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await q.put(ev)

    async def generate():
        task = asyncio.create_task(advice.stream_advice(symbol, emit, start, end))
        yield f'data: {json.dumps({"type": "phase", "agent": "start", "status": "running", "detail": "生成短线建议…"}, ensure_ascii=False)}\n\n'
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=12.0)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev.get("type") in ("result", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    if task.done() and q.empty():
                        break
            if task.done() and task.exception():
                err = str(task.exception())
                log.warning("advice stream 任务异常: %s", err)
                yield f'data: {json.dumps({"type": "error", "message": err}, ensure_ascii=False)}\n\n'
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
