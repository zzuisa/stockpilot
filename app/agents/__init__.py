"""多 Agent 运行时：supervisor（动态委派）+ 领域子 Agent。

- supervisor：`agents.supervisor.run_supervisor(...)` —— Qwen 工具循环，把能力总线按领域暴露，
  自主决定委派哪个子 Agent；复用 `_run_llm/_emit/_LLM_SEM`，接入 `analysis.thesis` 反思记忆。
- 子 Agent：作为注册表工具存在（`research`/`attribution` 即现有子图；`quant_advisor` 见 subagents.py）。
  导入本包会把子 Agent 工具注册进 `tools.REGISTRY`。
"""
from agents import subagents as subagents  # noqa: F401  —— import 即注册子 Agent 工具

__all__ = ["subagents"]
