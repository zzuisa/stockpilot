"""价格变动多 Agent 归因 — SSE 流式端点
驱动 price_attribution_agents 的 LangGraph，把各 Agent 的阶段/思考/最终结果
以 Server-Sent Events 推给前端(EventSource GET)。参照 api/stream.py 的 SSE 写法。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

import price_attribution_agents as paa
from db import get_db
from models import PriceAttribution

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/attribution", tags=["attribution"])


@router.get("/history")
async def history(symbol: str = Query(...), limit: int = Query(10, ge=1, le=50),
                  db=Depends(get_db)):
    """某标的的历史归因记录(按时间倒序，供回看)。"""
    rows = db.execute(
        select(PriceAttribution).where(PriceAttribution.symbol == symbol.upper())
        .order_by(PriceAttribution.created_at.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "start": r.start_ts.isoformat(), "end": r.end_ts.isoformat(),
             "created_at": r.created_at.isoformat(), "pct_change": r.pct_change,
             "result": r.result} for r in rows]


@router.get("/stream")
async def attribution_stream(
    request: Request,
    symbol: str = Query(...),
    start: str = Query(..., description="ISO8601 起始"),
    end: str = Query(..., description="ISO8601 结束"),
    force: bool = Query(False, description="true=忽略缓存重新分析"),
):
    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await q.put(ev)

    async def generate():
        task = asyncio.create_task(paa.run_attribution(symbol, start, end, emit, force=force))
        yield f'data: {json.dumps({"type": "phase", "agent": "start", "status": "running", "detail": "多 Agent 归因启动…"}, ensure_ascii=False)}\n\n'
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=12.0)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev.get("type") == "result":
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    if task.done() and q.empty():
                        break
            # 任务异常 → 下发错误
            if task.done() and task.exception():
                err = str(task.exception())
                log.warning("attribution stream 任务异常: %s", err)
                yield f'data: {json.dumps({"type": "error", "message": err}, ensure_ascii=False)}\n\n'
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
