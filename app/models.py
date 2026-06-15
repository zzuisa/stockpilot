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
    source = Column(Text, nullable=False)            # finnhub | rss
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
    type = Column(Text)
    status = Column(Text)
    filled_quantity = Column(Float)
    filled_value = Column(Float)
    fill_price = Column(Float)
    date_created = Column(TIMESTAMP(timezone=True))
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
    detail = Column(Text)


class T212WatchlistItem(Base):
    """用户自定义 T212 Watchlist（API 不暴露，本地维护）"""
    __tablename__ = "t212_custom_watchlist"
    ticker = Column(Text, primary_key=True)          # T212 ticker, e.g. NVDA_US_EQ
    name = Column(Text)                              # 显示名称
    added_at = Column(TIMESTAMP(timezone=True), default=utcnow)


# ════════════ 量化交易(tick 级波段策略) ════════════

class QuantStrategy(Base):
    """每个 symbol 一条策略配置;active=True 时引擎在跑(重启自动恢复)"""
    __tablename__ = "quant_strategies"
    symbol = Column(Text, primary_key=True)          # NVDA
    t212_ticker = Column(Text, nullable=False)       # NVDA_US_EQ
    params = Column(JSONB, nullable=False, default=dict)
    # params: {rsi_buy, rsi_sell, stop_loss, budget_eur, interval,
    #          max_trades_day}
    active = Column(Boolean, default=False)
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
