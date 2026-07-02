"""
T212 价格变动归因 —— 多 Agent 协同骨架 (LangGraph)
======================================================

场景:用户在 T212 行情页框选一段时间 [t_start, t_end],点击"分析该时段价格
变动原因",本流水线用多个专职 Agent 协同给出带置信度的归因,并把每一步的
"思考 + 进度"流式吐给前端浮窗。

架构(Supervisor 循环 + 条件路由 + 自省回炉):

    entry → quant_signal ──► orchestrator ──(pop next)──► investigator ──┐
                                  ▲                                      │
                                  └──────────────────────────────────────┘
                                  │ (plan 为空)
                                  ▼
                            synthesizer ──(置信度达标)──► END
                                  │
                                  └──(need_more)──► orchestrator  (回炉补查)

设计要点:
  * quant_signal 先跑,算出 regime(系统性/特异性),orchestrator 据此只派
    必要的调查 Agent → 省 token、防跑偏。
  * 顺序执行(非并行),天然对应前端"一步步显示"的进度体验,也避免并发写状态。
  * synthesizer 的 Critic 自省:置信度不足则回炉,循环有 MAX_LOOPS 上限保护。
  * 所有 LLM / 工具 / 你们已有 LLM 系统 的调用点都用 TODO 标出,替换即可。

运行(mock 模式,无需 API key):
    python price_attribution_agents.py
SSE 服务(前端对接):
    uvicorn price_attribution_agents:app --reload   # 需要 fastapi + uvicorn
"""
from __future__ import annotations

import json
import operator
import time
from typing import Annotated, Literal, TypedDict

from langgraph.graph import StateGraph, END

# ---------------------------------------------------------------------------
# 0. 可替换的接入点(替换这三处即可从 mock 切到生产)
# ---------------------------------------------------------------------------

def llm_json(system: str, user: str) -> dict:
    """调用大模型并要求返回 JSON。

    TODO(生产): 换成真实模型,例如
        from langchain_openai import ChatOpenAI            # 或火山方舟 SDK
        resp = ChatOpenAI(model="...").invoke(
            [{"role": "system", "content": system},
             {"role": "user",   "content": user}])
        return json.loads(strip_code_fence(resp.content))
    这里用规则化 mock,保证骨架可离线端到端跑通。
    """
    return _MOCK_LLM.route(system, user)


def fetch_cached_clues(symbol: str, window: dict) -> list[dict]:
    """取"你们已有 LLM 系统已采集并分析过的结果"作为线索种子。

    TODO(生产): 调你们的服务,返回结构约定为
        [{"ts": ..., "text": ..., "type": "news|filing|social",
          "sentiment": -1..1, "score": 0..1}, ...]
    """
    return [
        {"ts": window["start"], "text": f"{symbol} Q1 财报营收超预期,数据中心同比高增",
         "type": "news", "sentiment": 0.8, "score": 0.9},
        {"ts": window["start"], "text": "多家投行上调目标价",
         "type": "news", "sentiment": 0.6, "score": 0.7},
    ]


def fetch_price_features(symbol: str, window: dict) -> dict:
    """取选区的价量特征(由行情/缓存层预计算)。

    TODO(生产): 从 30 天缓存 K 线 + 实时价切片计算真实特征。
    """
    return {
        "pct_change": 0.061,      # 区间涨跌幅
        "gap": True,              # 是否跳空
        "overnight": True,        # 主要发生在隔夜
        "volume_z": 3.4,          # 成交量 z-score(异常)
        "index_corr": 0.31,       # 与所属指数相关性 → 路由依据
    }


# ---------------------------------------------------------------------------
# 1. 共享 State
# ---------------------------------------------------------------------------

class PriceExplainState(TypedDict, total=False):
    symbol: str
    window: dict                          # {start, end, granularity}
    price_features: dict
    market_corr: float
    regime: Literal["systematic", "idiosyncratic", "mixed"]
    cached_clues: list

    # 累加型字段用 reducer,节点只需返回增量
    evidence: Annotated[list, operator.add]
    progress: Annotated[list, operator.add]   # ← 前端流式消费

    plan: list                            # 待跑的调查 Agent 队列
    current: str | None                   # 本轮要跑的 Agent(None = 去归因)
    done_agents: list

    attribution: dict                     # {因子: 占比}
    residual: float                       # 无法解释占比
    confidence: float
    status: Literal["running", "need_more", "done"]
    loops: int
    final: str


MAX_LOOPS = 2   # 自省回炉次数上限


def _p(node: str, thinking: str, pct: int) -> dict:
    """构造一条前端进度事件。"""
    return {"node": node, "thinking": thinking, "pct": pct, "t": round(time.time(), 3)}


# ---------------------------------------------------------------------------
# 2. 提示词(多套)
# ---------------------------------------------------------------------------

PROMPTS = {
    "quant_signal": (
        "你是量化信号分析师。只依据提供的价量数据,不臆测新闻。判断:涨跌幅与"
        "显著性、跳空/隔夜/盘中归属、成交量是否异常、与指数相关性,据此给出 "
        "regime(systematic/idiosyncratic/mixed)。禁止编造原因。"
        '只输出 JSON:{"regime":..., "vol_anomaly":bool, "thinking":"一句话"}'
    ),
    "orchestrator": (
        "你是价格归因调查的调度者。你不自己解释,只决定下一步派哪些调查 Agent。"
        "规则:index_corr>0.7→systematic,必派 macro_sector,news 降权;"
        "index_corr<0.4→idiosyncratic,必派 news_event + fundamental;"
        "若归因阶段返回 need_more,只针对其证据缺口补派对应 Agent。"
        '只输出 JSON:{"dispatch":[agent...], "reason":"一句话"}'
    ),
    "news_event": (
        "你是事件归因分析师。给你【预分析线索】与【选区时间轴】。把每条线索对齐到"
        "具体时间戳,只保留与价格拐点时间吻合的,给出 time_fit(0-1)与 direction"
        "(利多/利空)。严格区分'发生在窗口内'与'导致了变动',时间不吻合一律降权,"
        "不得引入线索之外的传闻。"
    ),
    "fundamental": (
        "你是基本面分析师。核对选区内的财报、指引、评级变动、内部人交易、分红回购、"
        "监管文件,标注每项的方向与时间吻合度。"
    ),
    "macro_sector": (
        "你是宏观/板块分析师。判断该变动有多少来自指数/板块/利率/汇率/同业共振,"
        "回答'这是不是市场层面的事'。"
    ),
    "sentiment": (
        "你是情绪/资金流分析师。看散户情绪、空头持仓变化、期权流、异常换手、指数"
        "纳入/再平衡等技术性因素。"
    ),
    "synthesizer": (
        "你是首席归因师。综合全部证据输出解释。必须:(1)给出归因分配(各因子占比)"
        "并保留 residual 无法解释项;(2)每项附证据与置信度;(3)Critic 自查:是否把"
        "相关当因果?时间戳是否真对齐?是否单一来源孤证?(4)若总置信度<0.6 或有关键"
        "缺口,status=need_more 并指明缺什么。语气客观,不给买卖建议。"
        '输出 JSON:{attribution, residual, confidence, status, explanation_zh}'
    ),
}

INVESTIGATORS = {"news_event", "fundamental", "macro_sector", "sentiment"}


# ---------------------------------------------------------------------------
# 3. 节点实现
# ---------------------------------------------------------------------------

def node_quant_signal(state: PriceExplainState) -> dict:
    feats = fetch_price_features(state["symbol"], state["window"])
    out = llm_json(PROMPTS["quant_signal"], json.dumps(feats, ensure_ascii=False))
    regime = out.get("regime", "mixed")
    return {
        "price_features": feats,
        "market_corr": feats["index_corr"],
        "regime": regime,
        "cached_clues": fetch_cached_clues(state["symbol"], state["window"]),
        "loops": 0,
        "status": "running",
        "progress": [_p("quant_signal", out.get("thinking", f"regime={regime}"), 15)],
    }


def node_orchestrator(state: PriceExplainState) -> dict:
    plan = state.get("plan")
    # 首次进入:根据 regime 规划调查队列
    if plan is None:
        out = llm_json(
            PROMPTS["orchestrator"],
            json.dumps({"regime": state["regime"],
                        "index_corr": state["market_corr"]}, ensure_ascii=False),
        )
        plan = [a for a in out.get("dispatch", []) if a in INVESTIGATORS]
        prog = [_p("orchestrator", f"派发调查:{plan}", 25)]
    else:
        prog = []

    if plan:  # 还有待跑的 Agent → 弹出一个
        current, rest = plan[0], plan[1:]
        return {"plan": rest, "current": current, "progress": prog}
    # 队列空 → 去归因
    return {"plan": [], "current": None, "progress": prog}


def _run_investigator(name: str, state: PriceExplainState, pct: int) -> dict:
    ctx = {
        "window": state["window"],
        "clues": state.get("cached_clues", []),
        "price_features": state.get("price_features", {}),
    }
    out = llm_json(PROMPTS[name], json.dumps(ctx, ensure_ascii=False))
    items = out.get("evidence", [])
    thinking = out.get("thinking", f"{name} 完成,命中 {len(items)} 条证据")
    return {
        "evidence": [{"source": name, **it} for it in items],
        "done_agents": state.get("done_agents", []) + [name],
        "progress": [_p(name, thinking, pct)],
    }


def node_news_event(state):   return _run_investigator("news_event", state, 45)
def node_fundamental(state):  return _run_investigator("fundamental", state, 55)
def node_macro_sector(state): return _run_investigator("macro_sector", state, 65)
def node_sentiment(state):    return _run_investigator("sentiment", state, 75)


def node_synthesizer(state: PriceExplainState) -> dict:
    ctx = {"evidence": state.get("evidence", []),
           "price_features": state.get("price_features", {})}
    out = llm_json(PROMPTS["synthesizer"], json.dumps(ctx, ensure_ascii=False))
    loops = state.get("loops", 0)
    status = out.get("status", "done")

    # Critic 回炉:置信度不足且未超上限 → 补派缺口 Agent
    if status == "need_more" and loops < MAX_LOOPS:
        gap = [a for a in out.get("need_agents", ["macro_sector"])
               if a in INVESTIGATORS and a not in state.get("done_agents", [])]
        return {
            "status": "need_more",
            "plan": gap or None,          # None 会让 orchestrator 重新规划
            "loops": loops + 1,
            "progress": [_p("synthesizer",
                            f"置信度 {out.get('confidence', 0):.2f} 不足,回炉补查 {gap}", 80)],
        }

    return {
        "attribution": out.get("attribution", {}),
        "residual": out.get("residual", 0.0),
        "confidence": out.get("confidence", 0.0),
        "final": out.get("explanation_zh", ""),
        "status": "done",
        "progress": [_p("synthesizer",
                        f"归因完成,置信度 {out.get('confidence', 0):.2f}", 100)],
    }


# ---------------------------------------------------------------------------
# 4. 路由(条件边)
# ---------------------------------------------------------------------------

def route_from_orchestrator(state: PriceExplainState) -> str:
    return state["current"] if state.get("current") else "synthesizer"


def route_from_synthesizer(state: PriceExplainState) -> str:
    return "orchestrator" if state.get("status") == "need_more" else END


# ---------------------------------------------------------------------------
# 5. 组图
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(PriceExplainState)
    g.add_node("quant_signal", node_quant_signal)
    g.add_node("orchestrator", node_orchestrator)
    g.add_node("news_event", node_news_event)
    g.add_node("fundamental", node_fundamental)
    g.add_node("macro_sector", node_macro_sector)
    g.add_node("sentiment", node_sentiment)
    g.add_node("synthesizer", node_synthesizer)

    g.set_entry_point("quant_signal")
    g.add_edge("quant_signal", "orchestrator")
    g.add_conditional_edges(
        "orchestrator", route_from_orchestrator,
        {"news_event": "news_event", "fundamental": "fundamental",
         "macro_sector": "macro_sector", "sentiment": "sentiment",
         "synthesizer": "synthesizer"},
    )
    for inv in INVESTIGATORS:               # 每个调查 Agent 跑完回调度者
        g.add_edge(inv, "orchestrator")
    g.add_conditional_edges(
        "synthesizer", route_from_synthesizer,
        {"orchestrator": "orchestrator", END: END},
    )
    return g.compile()


GRAPH = build_graph()


def run(symbol: str, start: str, end: str):
    """一次性运行,返回最终 state(供非流式调用)。"""
    init = {"symbol": symbol,
            "window": {"start": start, "end": end, "granularity": "1d"}}
    return GRAPH.invoke(init)


def stream_progress(symbol: str, start: str, end: str):
    """流式运行,逐节点 yield 进度事件(供 SSE)。"""
    init = {"symbol": symbol,
            "window": {"start": start, "end": end, "granularity": "1d"}}
    for chunk in GRAPH.stream(init):
        for node, delta in chunk.items():
            for ev in delta.get("progress", []):
                yield ev


# ---------------------------------------------------------------------------
# 6. mock LLM(仅演示用,生产时删除)
# ---------------------------------------------------------------------------

class _MockLLM:
    def route(self, system: str, user: str) -> dict:
        if system.startswith("你是量化信号"):
            f = json.loads(user)
            corr = f.get("index_corr", 0.5)
            regime = ("idiosyncratic" if corr < 0.4
                      else "systematic" if corr > 0.7 else "mixed")
            return {"regime": regime, "vol_anomaly": f.get("volume_z", 0) > 2,
                    "thinking": f"与指数相关性 {corr},判为{regime}"}
        if system.startswith("你是价格归因调查的调度者"):
            ctx = json.loads(user)
            r = ctx.get("regime")
            if r == "idiosyncratic":
                d = ["news_event", "fundamental", "sentiment"]
            elif r == "systematic":
                d = ["macro_sector", "news_event"]
            else:
                d = ["news_event", "fundamental", "macro_sector", "sentiment"]
            return {"dispatch": d, "reason": f"{r} → {d}"}
        if system.startswith("你是事件归因"):
            return {"evidence": [
                {"event": "Q1 财报超预期", "ts": "t0", "time_fit": 0.9, "direction": "利多"},
                {"event": "投行上调目标价", "ts": "t0", "time_fit": 0.7, "direction": "利多"}],
                "thinking": "命中 2 条时间吻合的利多事件"}
        if system.startswith("你是基本面"):
            return {"evidence": [{"event": "指引上调", "time_fit": 0.8, "direction": "利多"}],
                    "thinking": "财报指引上调"}
        if system.startswith("你是宏观/板块"):
            return {"evidence": [{"event": "半导体板块普涨", "time_fit": 0.5, "direction": "利多"}],
                    "thinking": "板块贡献中等"}
        if system.startswith("你是情绪/资金流"):
            return {"evidence": [{"event": "纳入指数带来被动买入", "time_fit": 0.6, "direction": "利多"}],
                    "thinking": "有指数纳入资金流"}
        if system.startswith("你是首席归因师"):
            return {"attribution": {"财报超预期": 0.6, "板块普涨": 0.25},
                    "residual": 0.15, "confidence": 0.78, "status": "done",
                    "explanation_zh": ("该时段上涨主要由 Q1 财报超预期驱动(约 60%),"
                                       "叠加半导体板块普涨(约 25%),其余约 15% 无法明确归因。")}
        return {}


_MOCK_LLM = _MockLLM()


# ---------------------------------------------------------------------------
# 7. FastAPI SSE 端点(前端浮窗对接,选装)
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    app = FastAPI()

    @app.get("/explain/stream")
    def explain_stream(symbol: str, start: str, end: str):
        def gen():
            for ev in stream_progress(symbol, start, end):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
except ImportError:  # 未装 fastapi 也不影响核心逻辑
    app = None


# ---------------------------------------------------------------------------
# 8. 本地演示
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== 流式进度(前端浮窗会收到这些事件)===")
    for ev in stream_progress("MRVL", "2026-05-27", "2026-05-29"):
        print(f"[{ev['pct']:>3}%] {ev['node']:<13} {ev['thinking']}")

    print("\n=== 最终归因 ===")
    final = run("MRVL", "2026-05-27", "2026-05-29")
    print("归因:", json.dumps(final["attribution"], ensure_ascii=False))
    print("无法解释:", final["residual"])
    print("置信度:", final["confidence"])
    print("解释:", final["final"])
