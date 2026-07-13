"""Agent supervisor — SSE 流式端点。

驱动 agents.supervisor 的多 Agent 委派，把 supervisor/子 Agent 的阶段/思考/最终结果以
Server-Sent Events 推给前端（EventSource GET）。参照 api/attribution.py 的写法。
交互式默认只读（allow_write=False）；写权限走 Phase 5/6 的托管路径，不从此端点开放。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from db import get_db
from fastapi import Depends
from models import AgentRun

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.get("/history")
async def history(symbol: str = Query(...), limit: int = Query(10, ge=1, le=50),
                  db=Depends(get_db)):
    rows = db.execute(
        select(AgentRun).where(AgentRun.symbol == symbol.upper())
        .order_by(AgentRun.ts.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "ts": r.ts.isoformat(), "mode": r.mode,
             "trigger": r.trigger, "decision": r.decision,
             "outcome": r.outcome} for r in rows]


@router.get("/stream")
async def agent_stream(
    request: Request,
    symbol: str = Query(...),
    query: str = Query(..., description="用户问题；可带 side/持仓上下文"),
):
    from agents.supervisor import run_supervisor

    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict):
        await q.put(ev)

    async def generate():
        task = asyncio.create_task(
            run_supervisor(symbol, query, mode="interactive", emit=emit,
                           allow_write=False))
        yield ('data: ' + json.dumps(
            {"type": "phase", "agent": "supervisor", "status": "running",
             "detail": "Agent 启动…"}, ensure_ascii=False) + '\n\n')
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
            if task.done() and task.exception():
                err = str(task.exception())
                log.warning("agent stream 任务异常: %s", err)
                yield f'data: {json.dumps({"type": "error", "message": err}, ensure_ascii=False)}\n\n'
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
