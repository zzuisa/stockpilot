"""能力注册表单测。运行：python3 -m pytest app/tests/test_registry.py
或直接 python3 app/tests/test_registry.py。

只校验注册表结构与 dispatch 的容错语义（不触网、不依赖 DB/yfinance）——
真实数据类工具的 e2e 验证在 MCP/Agent 阶段做。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.catalog  # noqa: F401  —— import 即完成注册
from tools.registry import REGISTRY, Tool, dispatch, register, specs, tool


def test_catalog_registered():
    # Phase 1 至少登记这些核心只读工具
    for name in ("get_market_data", "quick_fact", "get_indicators",
                 "get_strategy_status", "attribution", "research"):
        assert name in REGISTRY, f"missing tool: {name}"


def test_all_read_risk_in_phase1():
    # Phase 1 catalog 必须全部只读（无写副作用）
    assert all(t.risk == "read" for t in REGISTRY.values()), \
        "Phase 1 catalog 出现了非 read 工具"


def test_schemas_are_valid_object_schemas():
    for t in REGISTRY.values():
        assert isinstance(t.params_schema, dict)
        assert t.params_schema.get("type") == "object", t.name
        assert "properties" in t.params_schema, t.name


def test_openai_spec_shape():
    t = REGISTRY["get_market_data"]
    spec = t.openai_spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "get_market_data"
    assert spec["function"]["parameters"] == t.params_schema


def test_specs_filter_by_risk_and_domain():
    assert len(specs(risk={"read"})) == len(REGISTRY)      # 全只读
    assert len(specs(risk={"write_order"})) == 0
    research = specs(domain="research")
    assert any(s["function"]["name"] == "attribution" for s in research)


def test_dispatch_unknown_tool_is_soft_error():
    out = asyncio.run(dispatch("no_such_tool", {}))
    assert isinstance(out, dict) and "error" in out


def test_dispatch_catches_handler_exception():
    @tool("boom_test", "raises", {"type": "object", "properties": {}})
    def _boom():
        raise RuntimeError("kaboom")
    out = asyncio.run(dispatch("boom_test"))
    assert out["error"].startswith("RuntimeError")
    del REGISTRY["boom_test"]


def test_dispatch_runs_sync_and_async():
    register(Tool("echo_sync", "e", {"type": "object", "properties": {}},
                  lambda **k: {"ok": "sync"}))

    async def _a(**k):
        return {"ok": "async"}
    register(Tool("echo_async", "e", {"type": "object", "properties": {}}, _a))
    assert asyncio.run(dispatch("echo_sync"))["ok"] == "sync"
    assert asyncio.run(dispatch("echo_async"))["ok"] == "async"
    del REGISTRY["echo_sync"], REGISTRY["echo_async"]


def test_duplicate_registration_rejected():
    try:
        register(Tool("get_market_data", "dup",
                      {"type": "object", "properties": {}}, lambda **k: None))
        assert False, "应拒绝重复注册"
    except ValueError:
        pass


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
