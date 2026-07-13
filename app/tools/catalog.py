"""把现有确定性能力包装成注册表工具（Phase 1：只读）。

只登记**无副作用**的能力。写类工具（建 OrderIntent、调整策略）在 Phase 5 引入并强制过
风控闸门。导入本模块即完成注册（`main.py` 启动时 import 一次即可）。

handler 内均用**惰性 import**，避免注册期触发重依赖 / 循环 import（quant、langchain 等
仅在真正调用工具时加载）。需要 db 会话的能力在包装里自开自关 `get_session()`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.registry import Tool, register

_FMT = "%Y-%m-%d %H:%M:%S"


def _sym(symbol: str) -> str:
    return symbol.split("_")[0].upper()


# ── market_data：最全的确定性快照（报价/基本面/估值/新鲜度/财报日/指标/趋势）──────────
def _get_market_data(symbol: str) -> dict:
    from analysis import market_data as md
    return md.build_market_data(_sym(symbol))


def _quick_fact(symbol: str, query: str):
    from analysis import market_data as md
    m = md.build_market_data(_sym(symbol))
    return md.quick_fact_answer(m, query) or {
        "note": "非快问快答类问题，请用 get_market_data / research 深入。"}


# ── 技术指标（日线 RSI/MACD/SMA/ATR/BB）──────────────────────────────────────────
def _get_indicators(symbol: str) -> dict:
    from db import get_session
    from analysis import indicators
    with get_session() as s:
        return indicators.compute_symbol(s, _sym(symbol)) or {
            "note": "无足够历史数据计算指标。"}


# ── 期权衍生指标（GEX/PCR/IV/Gamma Wall）─────────────────────────────────────────
def _get_options(symbol: str) -> dict:
    from analysis import options
    return options.option_metrics(_sym(symbol))


# ── 新闻/社区情绪聚合 ────────────────────────────────────────────────────────────
def _get_sentiment(symbol: str, days: int = 3) -> dict:
    from db import get_session
    from analysis import sentiment
    with get_session() as s:
        return sentiment.symbol_aggregates(s, _sym(symbol), int(days))


# ── 量化策略实时状态（读运行中的 StrategyRunner）───────────────────────────────────
def _get_strategy_status(symbol: str) -> dict:
    import quant
    r = quant.get_runner(_sym(symbol))
    return r.status() if r else {"symbol": _sym(symbol), "running": False,
                                 "note": "该标的当前无运行中的量化策略。"}


def _list_strategies() -> list[dict]:
    import quant
    return [r.status() for r in quant.list_runners()]


# ── ETF 组合回测 ─────────────────────────────────────────────────────────────────
def _run_backtest(cfg: dict) -> dict:
    from analysis import portfolio
    return portfolio.run_backtest(cfg or {})


# ── 价格归因子图（整条多 Agent 图当一个工具；自身会缓存结果，无对外副作用）────────────
async def _attribution(symbol: str, days: int = 20) -> dict:
    from price_attribution_agents import run_attribution
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days))
    return await run_attribution(_sym(symbol), start.strftime(_FMT),
                                 end.strftime(_FMT))


# ── 研究 Agent（意图路由 + 模板分析；自身缓存，无对外副作用）──────────────────────────
async def _research(symbol: str, query: str) -> dict:
    from equity_research_agents import run_research
    return await run_research(_sym(symbol), query)


# ── 富途情报（仅在 OpenD 可用时有值；不可用返回 {"unavailable": true}）──────────────────
def _futu_search_news(keyword: str, sub_type: str = "NEWS") -> dict:
    from analysis import futu_skills
    return futu_skills.search_news(keyword, sub_type)


def _futu_snapshot(code: str) -> dict:
    from analysis import futu_skills
    return futu_skills.snapshot(code)


_S_SYMBOL = {"type": "object",
             "properties": {"symbol": {"type": "string",
                                       "description": "标的代码，如 NIO / AAPL"}},
             "required": ["symbol"]}


def _register_all() -> None:
    """幂等注册（重复 import 不会因 duplicate 报错）。"""
    from tools.registry import REGISTRY
    specs = [
        Tool("get_market_data",
             "确定性市场快照：报价、基本面(TTM/最新季)、分析师目标+新鲜度、下次财报日(含"
             "confirmed/estimated 状态)、日线技术指标、趋势/资金。数字全部来自数据源，勿臆造。",
             _S_SYMBOL, _get_market_data, domain="research"),
        Tool("quick_fact",
             "快问快答：现价/下次财报日/PE/目标价等，直接取数不做推理。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "query": {"type": "string",
                                       "description": "如 ‘下次财报几号’ ‘现价多少’"}},
              "required": ["symbol", "query"]},
             _quick_fact, domain="research"),
        Tool("get_indicators", "日线技术指标：RSI/MACD/SMA50-200/ATR/布林/量比。",
             _S_SYMBOL, _get_indicators, domain="research"),
        Tool("get_options", "期权衍生指标：GEX/PCR/IV/Put-Call Wall/按行权价 Gamma。",
             _S_SYMBOL, _get_options, domain="research"),
        Tool("get_sentiment",
             "新闻+社区情绪聚合（默认近 3 天）。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "days": {"type": "integer", "default": 3}},
              "required": ["symbol"]},
             _get_sentiment, domain="research"),
        Tool("get_strategy_status",
             "读某标的运行中的量化策略实时状态：params、持仓、盈亏、胜率、最近动作与理由。",
             _S_SYMBOL, _get_strategy_status, domain="quant"),
        Tool("list_strategies", "列出全部运行中的量化策略及其状态。",
             {"type": "object", "properties": {}}, _list_strategies, domain="quant"),
        Tool("run_backtest",
             "跑 ETF 组合回测。cfg 为回测配置对象（标的/权重/区间/再平衡等）。",
             {"type": "object",
              "properties": {"cfg": {"type": "object",
                                     "description": "回测配置，见 analysis.portfolio.run_backtest"}},
              "required": ["cfg"]},
             _run_backtest, domain="quant"),
        Tool("attribution",
             "价格变动归因（多 Agent 子图）：解释某标的近 N 天为何涨跌，给主因+置信+证据。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "days": {"type": "integer", "default": 20}},
              "required": ["symbol"]},
             _attribution, domain="research"),
        Tool("research",
             "研究 Agent：对某标的的自由提问（估值/深度体检/财报解读/对比），返回结构化分析。",
             {"type": "object",
              "properties": {"symbol": {"type": "string"},
                             "query": {"type": "string"}},
              "required": ["symbol", "query"]},
             _research, domain="research"),
        Tool("futu_search_news",
             "富途资讯搜索（新闻/公告/评级）。OpenD 不可用时返回 {unavailable:true}。",
             {"type": "object",
              "properties": {"keyword": {"type": "string"},
                             "sub_type": {"type": "string",
                                          "enum": ["NEWS", "NOTICE", "RATING", "ALL"],
                                          "default": "NEWS"}},
              "required": ["keyword"]},
             _futu_search_news, domain="research"),
        Tool("futu_snapshot",
             "富途标的快照。code 如 HK.00700 / US.AAPL。OpenD 不可用返回 {unavailable:true}。",
             {"type": "object", "properties": {"code": {"type": "string"}},
              "required": ["code"]},
             _futu_snapshot, domain="research"),
    ]
    for t in specs:
        if t.name not in REGISTRY:
            register(t)


_register_all()
