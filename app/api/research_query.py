"""股票研究 Agent — SSE 流式端点
驱动 equity_research_agents（意图路由 + 模板 + 分层推理），把意图/新鲜度/思考/最终结果
以 Server-Sent Events 推给前端(EventSource GET)。事件 schema 与 api/attribution.py 一致。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

import equity_research_agents as era
from db import get_db
from models import ResearchQuery

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.get("/query/history")
async def history(symbol: str = Query(...), limit: int = Query(10, ge=1, le=50),
                  db=Depends(get_db)):
    """某标的的历史研究问答(按时间倒序，供回看)。"""
    rows = db.execute(
        select(ResearchQuery).where(ResearchQuery.symbol == symbol.upper())
        .order_by(ResearchQuery.created_at.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "query": r.query, "template": r.template,
             "created_at": r.created_at.isoformat(), "result": r.result} for r in rows]


@router.get("/query/stream")
async def research_stream(
    request: Request,
    symbol: str = Query(...),
    q: str = Query(..., description="用户自由提问"),
    force: bool = Query(False, description="true=忽略缓存重新分析"),
):
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await queue.put(ev)

    async def generate():
        task = asyncio.create_task(era.run_research(symbol, q, emit, force=force))
        yield f'data: {json.dumps({"type": "phase", "agent": "start", "status": "running", "detail": "研究 Agent 启动…"}, ensure_ascii=False)}\n\n'
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=12.0)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev.get("type") == "result":
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    if task.done() and queue.empty():
                        break
            if task.done() and task.exception():
                err = str(task.exception())
                log.warning("research stream 任务异常: %s", err)
                yield f'data: {json.dumps({"type": "error", "message": err}, ensure_ascii=False)}\n\n'
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
