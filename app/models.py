"""SQLAlchemy ORM — 说明书 §5 全部表(v1 基础表 + v2 新增表)"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, Float, ForeignKey, Index, Integer,
    SmallInteger, String, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


def new_intent_id():
    return uuid.uuid4().hex


# ════════════ v1 基础表(§5.1) ════════════

class Price(Base):
    __tablename__ = "prices"
    symbol = Column(Text, primary_key=True)
    interval = Column(Text, primary_key=True, default="1d")   # '1d' | '5m'
    ts = Column(TIMESTAMP(timezone=True), primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)


class News(Base):
    __tablename__ = "news"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, index=True)                # 空 = 宏观新闻
    source = Column(Text, nullable=False)            # 采集渠道: finnhub | rss | alphavantage
    source_name = Column(Text)                       # 媒体名: Reuters/Bloomberg/...
    source_tier = Column(SmallInteger)               # 质量等级 1/2/3 (1 最佳)
    relevance = Column(Float)                        # 0..1 与标的相关度
    url = Column(Text, unique=True, nullable=False)
    title = Column(Text)
    summary = Column(Text)
    published = Column(TIMESTAMP(timezone=True))
    sentiment = Column(SmallInteger)                 # -2..+2,LLM 填
    llm_reason = Column(Text)
    fetched_at = Column(TIMESTAMP(timezone=True), default=utcnow)


class PositionSnapshot(Base):
    __tablename__ = "positions_snapshot"
    ts = Column(TIMESTAMP(timezone=True), primary_key=True, default=utcnow)
    ticker = Column(Text, primary_key=True)          # T212 ticker
    quantity = Column(Float)
    average_price = Column(Float)
    current_price = Column(Float)
    ppl = Column(Float)                              # 浮动盈亏
    fx_ppl = Column(Float)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshot"
    ts = Column(TIMESTAMP(timezone=True), primary_key=True, default=utcnow)
    free_cash = Column(Float)
    invested = Column(Float)
    total = Column(Float)
    ppl = Column(Float)
    result = Column(Float)                           # 已实现


class OrderIntent(Base):
    __tablename__ = "order_intents"
    id = Column(String(32), primary_key=True, default=new_intent_id)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=utcnow, onupdate=utcnow)
    symbol = Column(Text, nullable=False)
    t212_ticker = Column(Text)
    group_id = Column(Text)
    side = Column(Text, nullable=False)              # buy | sell
    rule = Column(Text)
    order_value_eur = Column(Float)
    quantity = Column(Float)
    price_at_signal = Column(Float)
    status = Column(Text, default="pending", index=True)
    # pending → confirmed → executed | failed;或 skipped / expired / rejected
    status_reason = Column(Text)
    confirmed_by = Column(Text)
    executed_order_id = Column(Text)
    expires_at = Column(TIMESTAMP(timezone=True))


class Signal(Base):
    __tablename__ = "signals"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow)
    symbol = Column(Text, nullable=False, index=True)
    rule = Column(Text, nullable=False)
    direction = Column(Text, nullable=False)         # buy | exit | alert
    strength = Column(Float)
    details = Column(JSONB, default=dict)
    pushed = Column(Boolean, default=False)


class T212Order(Base):
    __tablename__ = "t212_orders"
    id = Column(BigInteger, primary_key=True)        # T212 订单 id
    ticker = Column(Text)
    side = Column(Text)                              # BUY | SELL（真实成交方向）
    type = Column(Text)                              # MARKET | LIMIT | STOP
    status = Column(Text)                            # FILLED | CANCELLED | ...
    filled_quantity = Column(Float)                  # 真实成交股数
    filled_value = Column(Float)                     # 账户币种实际净额(含 FX/费, walletImpact.netValue)
    fill_price = Column(Float)                       # 标的币种真实成交单价
    date_created = Column(TIMESTAMP(timezone=True))  # 下单时间
    filled_at = Column(TIMESTAMP(timezone=True), index=True)  # 真实成交时间
    account_id = Column(Integer, index=True)         # 所属 T212 账户
    raw = Column(JSONB)


class T212Dividend(Base):
    __tablename__ = "t212_dividends"
    reference = Column(Text, primary_key=True)
    ticker = Column(Text)
    amount = Column(Float)
    amount_in_euro = Column(Float)
    paid_on = Column(TIMESTAMP(timezone=True))


# ════════════ v2 新增:Watchlist / Group / 路由(§5.2) ════════════

class Group(Base):
    __tablename__ = "groups"
    id = Column(Text, primary_key=True)              # 'core_holdings'
    name = Column(Text, nullable=False)
    description = Column(Text)
    config = Column(JSONB, nullable=False, default=dict)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol = Column(Text, primary_key=True)          # 'NVDA'
    group_id = Column(Text, ForeignKey("groups.id", ondelete="CASCADE"),
                      primary_key=True)
    t212_ticker = Column(Text)
    tags = Column(ARRAY(Text), default=list)
    symbol_config = Column(JSONB, default=dict)      # symbol 级覆盖
    active = Column(Boolean, default=True)


class NotifyRoute(Base):
    __tablename__ = "notify_routes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Text, ForeignKey("groups.id", ondelete="CASCADE"),
                      nullable=False)
    symbol = Column(Text)                            # 空 = 组级路由
    channel = Column(Text, nullable=False)           # telegram | email
    recipient = Column(Text, nullable=False)         # chat_id 或 email
    event_types = Column(ARRAY(Text), nullable=False)
    active = Column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("group_id", "symbol", "channel", "recipient",
                         name="uq_notify_route"),
    )


# ════════════ v2 新增:T212 社区(§5.3) ════════════

class T212CommunityPost(Base):
    __tablename__ = "t212_community"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    topic_id = Column(BigInteger, nullable=False)
    post_id = Column(BigInteger, unique=True, nullable=False)
    symbol = Column(Text, nullable=False)
    author = Column(Text)
    content = Column(Text)
    published = Column(TIMESTAMP(timezone=True))
    likes = Column(Integer, default=0)
    sentiment = Column(SmallInteger)                 # -2..+2,LLM 填
    llm_summary = Column(Text)
    fetched_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    __table_args__ = (
        Index("idx_t212c_symbol_ts", "symbol", published.desc()),
    )


# ════════════ v2 新增:推送日志(§5.4) ════════════

class NotifyLog(Base):
    __tablename__ = "notify_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    event_type = Column(Text, nullable=False)        # daily_report | signal | news_shock
    group_id = Column(Text)
    symbol = Column(Text)
    channel = Column(Text, nullable=False)
    recipient = Column(Text, nullable=False)
    status = Column(Text, nullable=False)            # sent | failed | skipped
    error_msg = Column(Text)
    payload_hash = Column(Text, index=True)          # 防重发


# ════════════ 指标与运行记录(看板/信号引擎用) ════════════

class IndicatorDaily(Base):
    __tablename__ = "indicators_daily"
    symbol = Column(Text, primary_key=True)
    ts = Column(Date, primary_key=True)
    close = Column(Float)
    rsi = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_hist = Column(Float)
    macd_cross = Column(SmallInteger, default=0)     # 1=金叉 -1=死叉
    sma20 = Column(Float)
    sma50 = Column(Float)
    sma200 = Column(Float)
    atr = Column(Float)
    bb_upper = Column(Float)
    bb_lower = Column(Float)
    vol_ratio = Column(Float)


class JobRun(Base):
    __tablename__ = "job_runs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_name = Column(Text, nullable=False, index=True)
    started_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    finished_at = Column(TIMESTAMP(timezone=True))
    status = Column(Text, default="running")         # running | ok | failed | skipped
    progress = Column(Text)                          # 运行中实时进度文本(如 "采集 NVDA 3/11")
    detail = Column(Text)


class DataUpdate(Base):
    """数据更新流：新闻/情绪/信号等每次更新写一条，供应用内通知中心实时展示。"""
    __tablename__ = "data_updates"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    kind = Column(Text, nullable=False)              # news | sentiment | signal | trade
    symbol = Column(Text, index=True)                # 空 = 全局/宏观
    title = Column(Text)                             # 一行摘要
    detail = Column(JSONB)


# ════════════ T212 多账户 ════════════

class T212Account(Base):
    """多组 T212 API Key，支持账户切换"""
    __tablename__ = "t212_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)              # 显示名称，如 "Demo - 个人"
    api_key = Column(Text, nullable=False)           # API Key
    api_secret = Column(Text, nullable=True)         # API Secret（部分接口需要，Base64 鉴权用）
    env = Column(Text, nullable=False, default="demo")   # demo | live
    is_active = Column(Boolean, default=False)       # 同一时刻只有一个账户激活
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)


class T212WatchlistItem(Base):
    """用户自定义 T212 Watchlist（API 不暴露，本地维护）"""
    __tablename__ = "t212_custom_watchlist"
    ticker = Column(Text, primary_key=True)          # T212 ticker, e.g. NVDA_US_EQ
    name = Column(Text)                              # 显示名称
    added_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    account_id = Column(Integer, ForeignKey("t212_accounts.id", ondelete="SET NULL"),
                        nullable=True, index=True)   # NULL = 迁移前历史数据


# ════════════ 量化交易(tick 级波段策略) ════════════

class QuantStrategy(Base):
    """每个 symbol 一条策略配置;active=True 时引擎在跑(重启自动恢复)"""
    __tablename__ = "quant_strategies"
    symbol = Column(Text, primary_key=True)          # NVDA
    t212_ticker = Column(Text, nullable=False)       # NVDA_US_EQ
    params = Column(JSONB, nullable=False, default=dict)
    active = Column(Boolean, default=False)
    account_id = Column(Integer, ForeignKey("t212_accounts.id", ondelete="SET NULL"),
                        nullable=True, index=True)   # 关联账户
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=utcnow,
                        onupdate=utcnow)


class QuantTrade(Base):
    """量化策略成交记录"""
    __tablename__ = "quant_trades"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    symbol = Column(Text, nullable=False, index=True)
    t212_ticker = Column(Text)
    side = Column(Text, nullable=False)              # buy | sell
    reason = Column(Text)        # ind_buy | profit_sell | signal_stop | hard_stop | manual_stop
    quantity = Column(Float)
    price = Column(Float)                            # 触发时价格
    pnl = Column(Float)                              # 卖出时的估算盈亏(USD)
    order_id = Column(Text)                          # T212 订单 id
    detail = Column(JSONB)                           # 触发时指标快照
    account_id = Column(Integer, ForeignKey("t212_accounts.id", ondelete="SET NULL"),
                        nullable=True, index=True)   # 关联账户


class EarningsReport(Base):
    """季报/年报下载记录"""
    __tablename__ = "earnings_reports"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    period = Column(Text, nullable=False)              # e.g. "2024Q4"
    downloaded_at = Column(TIMESTAMP(timezone=True), default=utcnow)
    filename = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    size = Column(Integer)
    source = Column(Text, default="yfinance")
    __table_args__ = (
        UniqueConstraint("symbol", "period", name="uq_earnings_report"),
    )


class InvestmentAnalysis(Base):
    """LLM 深度分析结果(泡沫分析 + 投资策略)"""
    __tablename__ = "investment_analysis"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    analysis_type = Column(Text, nullable=False)       # "bubble" | "strategy"
    bubble_level = Column(Text)                        # normal|slight|moderate|severe|extreme
    bubble_pct = Column(Float)
    strategy_text = Column(Text)                       # LLM 结果 JSON string
    tokens = Column(Integer, default=0)
    key_metrics = Column(JSONB)
    report_period = Column(Text)


class NewsBrief(Base):
    """单股新闻精华(LLM 高信号筛选 + 投资判断),只推送此结果而非原始标题。"""
    __tablename__ = "news_briefs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    window_hours = Column(Integer)                   # 覆盖的新闻时间窗(小时)
    headline = Column(Text)                          # 一句话核心判断
    sentiment = Column(Text)                         # bullish | bearish | neutral
    judgment = Column(Text)                          # 投资判断段落
    summary_md = Column(Text)                        # 按类别精华 markdown
    watch_points = Column(Text)                      # 后续需关注的事件/数据
    item_count = Column(Integer, default=0)          # 纳入的新闻条数
    news_ids = Column(JSONB)                         # 关联 news.id 列表
    tokens = Column(Integer, default=0)
    pushed = Column(Boolean, default=False)


class PriceAttribution(Base):
    """价格变动多 Agent 归因结果(按 标的+时间窗口 缓存复用 + 可回看历史)。"""
    __tablename__ = "price_attributions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    start_ts = Column(TIMESTAMP(timezone=True), nullable=False)
    end_ts = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    pct_change = Column(Float)                        # 窗口内涨跌%
    result = Column(JSONB)                            # 最终归因(synth 输出)
    agents = Column(JSONB)                            # 各 Agent 思考文本(基本面/技术面/情绪/质疑)
    tokens = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("symbol", "start_ts", "end_ts",
                                       name="uq_attr_window"),)


class ResearchQuery(Base):
    """股票研究 Agent 的问答结果(按 标的+问题hash 缓存复用 + 可回看历史)。"""
    __tablename__ = "research_queries"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    query = Column(Text, nullable=False)              # 用户原始问题
    query_hash = Column(Text, nullable=False)         # 归一化问题的 sha1[:16]
    template = Column(Text)                           # 意图模板(valuation/attribution/…)
    created_at = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    result = Column(JSONB)                            # {template, answer, market_data, freshness, …}
    tokens = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("symbol", "query_hash",
                                       name="uq_research_symbol_query"),)


class AdviceResult(Base):
    """短线投资建议结果(右侧常备面板)。持久化 + 缓存，每标的仅保留最新 5 条。"""
    __tablename__ = "advice_results"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    window_start = Column(TIMESTAMP(timezone=True))
    window_end = Column(TIMESTAMP(timezone=True))
    is_realtime = Column(Boolean, default=False)
    stance = Column(Text)                             # 偏多 | 偏空 | 中性
    result = Column(JSONB)                            # 完整结构化建议(含 meta)
    reasoning = Column(Text)                          # 流式推理过程
    tokens = Column(Integer, default=0)


class TradeLog(Base):
    """统一交易历史：应用经手的所有下单(手动 UI + 量化循环)"""
    __tablename__ = "trade_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(TIMESTAMP(timezone=True), default=utcnow, index=True)
    source = Column(Text, nullable=False)            # manual | quant
    symbol = Column(Text, index=True)                # 简码 NVDA
    t212_ticker = Column(Text)                       # NVDA_US_EQ
    side = Column(Text, nullable=False)              # buy | sell
    order_type = Column(Text)        # market | limit | stop | band_sell | band_buy
    quantity = Column(Float)
    price = Column(Float)                            # 限价/止损价 或 估算成交价
    value_eur = Column(Float)                        # 按金额下单时的金额
    currency = Column(Text)                          # 按金额下单所选币种 USD|EUR
    pnl = Column(Float)                              # 卖出估算盈亏(仅量化成交有)
    reason = Column(Text)                            # 量化原因 / 手动
    status = Column(Text)                            # submitted | filled | failed
    order_id = Column(Text)                          # T212 订单 id
    env = Column(Text)                               # demo | live
    detail = Column(JSONB)
