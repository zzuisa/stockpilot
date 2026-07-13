"""能力总线（Capability Registry）——Agent 化重构的脊柱。

一次声明每个能力（name/schema/handler/risk/confirm），三处复用：
MCP server（`mcp_server/`）、Agent 运行时（`agents/`）、以及现有 HTTP API。
导入 `tools.catalog` 会把现有 `analysis/*`、`quant`、研究子图注册进全局 `REGISTRY`。
"""
from tools.registry import (REGISTRY, Tool, dispatch, get, register, specs,
                            tool)
from tools import catalog as catalog  # noqa: F401  —— 只读能力注册进 REGISTRY
from tools import write_catalog as write_catalog  # noqa: F401  —— 写类能力(过风控闸门)

__all__ = ["REGISTRY", "Tool", "catalog", "write_catalog", "dispatch", "get",
           "register", "specs", "tool"]
