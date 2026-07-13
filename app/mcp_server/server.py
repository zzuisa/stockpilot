"""由能力注册表自动生成的 MCP server（HTTP/SSE 传输）。

- 工具来源：`tools.REGISTRY`。Phase 2 只暴露 `read` 类工具；写类（下单/改策略）在 Phase 5
  连同 confirm 语义一起开放（改 `EXPOSED_RISK` 即可）。
- 传输：低层 `mcp.server.lowlevel.Server` + SSE，挂成一个 Starlette 子应用，`app.mount("/mcp", ...)`。
- 鉴权：`Authorization: Bearer <MCP_AUTH_TOKEN>` 或 `?token=`，`hmac.compare_digest` 常时比较。
- 健壮性：`mcp` 未安装 / 未配置 token → `mount_mcp` 记一条 warning 后返回，应用照常启动。
"""
from __future__ import annotations

import hmac
import json
import logging

import settings

log = logging.getLogger(__name__)

# 默认只读；写类是否经 MCP 暴露由设置 mcp_expose_write 动态决定（见 _exposed_risk）。
_WRITE_RISK = {"write_order", "write_strategy"}


def _exposed_risk() -> set[str]:
    risk = {"read"}
    try:
        from analysis import appsettings
        if appsettings.mcp_expose_write():
            risk |= _WRITE_RISK
    except Exception:                            # noqa: BLE001
        pass
    return risk


def _authorized(headers: dict, query_token: str | None) -> bool:
    token = settings.MCP_AUTH_TOKEN
    if not token:
        return False
    presented = query_token
    auth = headers.get("authorization") or headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        presented = auth[7:].strip()
    return bool(presented) and hmac.compare_digest(presented, token)


def _build_server():
    """构造低层 MCP Server，把注册表反射成 list_tools/call_tool。"""
    from mcp.server.lowlevel import Server
    import mcp.types as types

    from tools import REGISTRY, dispatch

    server = Server("stockpilot")

    @server.list_tools()
    async def _list_tools():
        risk = _exposed_risk()
        return [types.Tool(name=t.name, description=t.description,
                           inputSchema=t.params_schema)
                for t in REGISTRY.values() if t.risk in risk]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        t = REGISTRY.get(name)
        if not t or t.risk not in _exposed_risk():
            payload = {"error": f"tool not exposed over MCP: {name}"}
        else:
            payload = await dispatch(name, arguments or {})
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    return server


def mount_mcp(app) -> bool:
    """把 MCP SSE 端点挂到 FastAPI `app` 的 /mcp 下。返回是否成功挂载。

    端点：GET /mcp/sse（建立 SSE 会话）、POST /mcp/messages/（客户端→服务端消息）。
    外部 Agent 连 `https://<host><root_path>/mcp/sse`，带 Bearer token。
    """
    if not settings.mcp_enabled:
        log.info("MCP 未启用（未配置 MCP_AUTH_TOKEN），跳过挂载。")
        return False
    try:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route
    except Exception as e:                       # noqa: BLE001
        log.warning("MCP 依赖缺失，跳过挂载（pip install mcp）：%s", e)
        return False

    server = _build_server()
    sse = SseServerTransport("/mcp/messages/")

    def _guard(request) -> bool:
        return _authorized(dict(request.headers),
                           request.query_params.get("token"))

    async def handle_sse(request):
        if not _guard(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with sse.connect_sse(request.scope, request.receive,
                                   request._send) as (read, write):
            await server.run(read, write,
                             server.create_initialization_options())

    async def handle_messages(scope, receive, send):
        # POST 消息端点：先做一次轻量 token 校验（从 header/query 取）
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        qs = scope.get("query_string", b"").decode()
        qtok = None
        for kv in qs.split("&"):
            if kv.startswith("token="):
                qtok = kv[6:]
        if not _authorized(headers, qtok):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(
                scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)

    mcp_app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=handle_messages),
    ])
    app.mount("/mcp", mcp_app)
    risk = _exposed_risk()
    n = sum(1 for t in _registry_tools() if t.risk in risk)
    log.info("MCP 已挂载 /mcp/sse，当前暴露 %d 个工具（写暴露=%s）。",
             n, "write_order" in risk)
    return True


def _registry_tools():
    from tools import REGISTRY
    return list(REGISTRY.values())
