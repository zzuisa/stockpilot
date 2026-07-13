"""MCP server：把能力注册表（`tools.REGISTRY`）反射成 MCP tools，经 HTTP/SSE 暴露给外部
Agent（Claude Desktop/Code 等）。挂在 FastAPI 上，token 鉴权。

`mount_mcp(app)` 在 `main.py` 里调用；`mcp` 包缺失或未配置 token 时安静跳过，不拖垮启动。
"""
from mcp_server.server import mount_mcp

__all__ = ["mount_mcp"]
