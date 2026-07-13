"""能力注册表：一次声明、三处复用（MCP / Agent / HTTP）。

设计要点：
- 框架无关：只依赖标准库。`params_schema` 用 JSON Schema，OpenAI function-calling 与
  MCP 都能直接消费（`openai_spec()` / `mcp` 反射）。
- `dispatch()` 统一入口：sync handler 丢线程池、async handler 直接 await，异常收敛成
  `{"error": ...}`（Agent 循环里不会因单个工具炸掉）。
- `risk` 分级是**架构护栏**：`read` 无副作用；`write_order` 只能建 OrderIntent（永不裸下单）；
  `write_strategy` 只能经 `quant.start` 热切换。MCP/Agent 据此决定是否需要确认。

泛化自 `analysis/research_agent.py` 现有的 `_TOOLS`/`_DISPATCH` 手写范式。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

log = logging.getLogger(__name__)

Risk = Literal["read", "write_order", "write_strategy"]


@dataclass
class Tool:
    name: str
    description: str
    params_schema: dict                 # JSON Schema（type=object）
    handler: Callable[..., Any]         # 以 handler(**args) 调用；可 sync 或 async
    risk: Risk = "read"
    confirm_required: bool = False       # 写类工具默认需人工/托管策略确认
    domain: str = "general"             # 供 supervisor 按领域分组（research/quant/trading/...）

    def openai_spec(self) -> dict:
        """OpenAI / Qwen function-calling 工具规格。"""
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.params_schema}}


REGISTRY: dict[str, Tool] = {}


def register(t: Tool) -> Tool:
    if t.name in REGISTRY:
        raise ValueError(f"duplicate tool name: {t.name}")
    REGISTRY[t.name] = t
    return t


def tool(name: str, description: str, params_schema: dict, *,
         risk: Risk = "read", confirm_required: bool = False,
         domain: str = "general") -> Callable:
    """装饰器：把一个函数登记为工具。函数签名即 handler。"""
    def deco(fn: Callable) -> Callable:
        register(Tool(name, description, params_schema, fn,
                      risk, confirm_required, domain))
        return fn
    return deco


def get(name: str) -> Tool | None:
    return REGISTRY.get(name)


async def dispatch(name: str, args: dict | None = None) -> Any:
    """统一调用入口。未知工具 / 异常都收敛为 {"error": ...}，绝不抛给 Agent 循环。"""
    args = args or {}
    t = REGISTRY.get(name)
    if not t:
        return {"error": f"unknown tool: {name}"}
    try:
        if inspect.iscoroutinefunction(t.handler):
            return await t.handler(**args)
        return await asyncio.to_thread(lambda: t.handler(**args))
    except Exception as e:                # noqa: BLE001 —— 工具级容错是刻意的
        log.warning("tool %s(%s) failed: %s", name, args, e)
        return {"error": f"{type(e).__name__}: {e}"}


def specs(risk: set[str] | None = None, domain: str | None = None) -> list[dict]:
    """导出工具规格（OpenAI 格式）。可按 risk / domain 过滤。

    默认（risk=None）导出全部；MCP 只读暴露用 `specs(risk={"read"})`。
    """
    out = []
    for t in REGISTRY.values():
        if risk is not None and t.risk not in risk:
            continue
        if domain is not None and t.domain != domain:
            continue
        out.append(t.openai_spec())
    return out
