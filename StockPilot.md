# 美股助手完整实现方案 v2(StockPilot)

> 目标环境:4C16G 云服务器,已运行 Alist / Immich / CMS / Dify
> 定位:**数据采集 → 指标与 LLM 分析 → 看板与推送 → 人工确认的半自动交易**
> 原则:低频、轻资源、所有交易动作必须可审计、默认人工确认

---

## 0. 目标与边界

**做什么**
1. 自动同步 Trading212 账户(持仓、订单、分红、现金流),生成账户复盘看板
2. 采集行情(日线 + 自选股分钟线)与新闻,入库
3. **T212 社区情绪采集**:对 watchlist 中每只股票,抓取 Trading212 社区论坛的讨论帖与评论,优先分析正向情绪与催化剂
4. 每日收盘后:技术指标 + LLM 新闻情绪分析 → 生成早报推送
5. **多通道推送**:每只股票/每个组可独立配置收件人(Telegram / Email),支持一对多
6. **分组管理**:watchlist 按 Group 组织,每个 Group 绑定专属推送通道与收件人列表
7. 信号触发时推送确认卡片,点击后经风控校验调用 T212 API 下市价单
8. 策略先回测、再模拟盘(demo 环境)、最后小仓位实盘

**不做什么(明确边界)**
- 不做高频/盘中秒级策略:T212 不提供行情 API,行情来自第三方,延迟在分钟级
- 不做全自动无人值守交易(至少前 3 个月)
- 不依赖 LLM 直接给出买卖决策,LLM 只产出结构化情绪分与摘要,决策由规则引擎 + 人完成

---

## 1. 总体架构

```
                  ┌────────────────────────────────────────────────────┐
 第三方数据源      │                    你的服务器 (4C16G)               │
┌──────────────┐  │  ┌───────────────── stockpilot ─────────────────┐ │
│ yfinance     │──┼─▶│                                              │ │
│ Finnhub      │──┼─▶│ collector ── scheduler (APScheduler)         │ │
│ RSS/RSSHub   │──┼─▶│   │                                         │ │
│ T212 社区    │──┼─▶│   ▼                                         │ │
└──────────────┘  │  │ TimescaleDB ◀── t212_sync                   │ │
                  │  │   │    ▲                                     │ │
┌──────────────┐  │  │   │    │ signals / sentiment                │ │
│ Trading212   │◀─┼──┼───┼── analyzer ──▶ Dify API (已有)          │ │
│  API         │  │  │   │    │                                     │ │
└──────────────┘  │  │   │    ▼                                     │ │
      ▲           │  │   │  router ──▶ 查 groups + recipients      │ │
      │           │  │   │    ├──▶ Telegram Bot (多 chat_id)       │ │
      │           │  │   │    ├──▶ SMTP Email (多收件人)            │ │
      │           │  │   │    └──▶ 确认按钮回调                     │ │
      └───────────┼──┼───┼── executor ◀───┘ (风控校验)              │ │
                  │  │   ▼                                         │ │
                  │  │ Grafana 看板 + FastAPI 管理接口              │ │
                  │  └─────────────────────────────────────────────┘ │
                  └────────────────────────────────────────────────────┘
```

六个核心模块 + 一个管理接口,全部跑在一个 Python 应用容器里,外加 TimescaleDB 和 Grafana。

---

## 2. 资源预算

| 容器 | 内存上限 | 说明 |
|---|---|---|
| timescaledb | 1 GB | shared_buffers=256MB |
| grafana | 300 MB | |
| stockpilot(app) | 700 MB | 采集+分析+bot+邮件,单容器 |
| 合计 | **< 2 GB** | 不新增向量库,复用 Dify 的 LLM 能力 |

---

## 3. 目录结构

```
stockpilot/
├── docker-compose.yml
├── .env
├── config/
│   └── watchlist.yaml            # ★ Watchlist / Group / 收件人配置(核心)
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # FastAPI + APScheduler 入口
│   ├── config.py                 # 加载 watchlist.yaml → 内存模型
│   ├── db.py
│   ├── models.py                 # SQLAlchemy ORM(所有表)
│   ├── t212/
│   │   ├── client.py             # T212 API 客户端
│   │   ├── sync.py               # 持仓/订单同步
│   │   └── community.py          # ★ T212 社区帖子采集
│   ├── collectors/
│   │   ├── prices.py
│   │   └── news.py               # Finnhub + RSS + T212 社区聚合
│   ├── analysis/
│   │   ├── indicators.py
│   │   ├── sentiment.py          # Dify workflow(含社区情绪)
│   │   └── signals.py
│   ├── trading/
│   │   ├── risk.py
│   │   └── executor.py
│   ├── notify/
│   │   ├── router.py             # ★ 根据 group/symbol 查收件人,分发
│   │   ├── telegram.py           # Telegram 多 chat_id 推送
│   │   └── email.py              # ★ SMTP 邮件推送
│   ├── api/
│   │   ├── watchlist.py          # ★ CRUD: group / symbol / recipient
│   │   ├── dashboard.py          # 看板数据 API
│   │   └── webhook.py            # Telegram callback
│   └── backtest/
│       └── run_backtest.py
├── grafana/
│   └── provisioning/
└── tests/
```

---

## 4. watchlist.yaml — 核心配置文件

这是整个系统的控制面。所有 group、symbol、收件人、推送通道都在这里定义,系统启动时加载到数据库,也可以通过 API 动态修改后回写。

```yaml
# ─── 全局默认 ───
defaults:
  notify_channels: [telegram]          # telegram | email | both
  telegram_chat_ids: ["${TELEGRAM_CHAT_ID}"]  # 你自己的主 chat
  email_recipients: []
  news_sources: [finnhub, rss]         # 默认新闻来源
  t212_community: true                 # 是否采集 T212 社区
  community_priority: positive         # positive | negative | all
  language: zh                         # 早报语言

# ─── 分组定义 ───
groups:

  # ─────────────────── 核心持仓 ───────────────────
  - id: core_holdings
    name: "核心持仓"
    description: "长期看好、重仓标的"
    notify_channels: [telegram, email]
    telegram_chat_ids:
      - "${TELEGRAM_CHAT_ID}"          # 你自己
      - "-1001234567890"               # 投资交流群
    email_recipients:
      - "ao@roguelife.de"
      - "buddy@example.com"            # 交流的朋友
    # 本组特有:信号触发时也推邮件存档
    notify_on: [daily_report, signal, news_shock]
    symbols:
      - ticker: NVDA
        t212_ticker: NVDA_US_EQ
        # 覆盖组级设置:NVDA 的新闻单独多推一个人
        extra_email: ["nvda-watcher@example.com"]
        tags: [ai, semiconductor]
      - ticker: MSFT
        t212_ticker: MSFT_US_EQ
        tags: [cloud, ai]
      - ticker: AAPL
        t212_ticker: AAPL_US_EQ
        tags: [consumer]

  # ─────────────────── 短线观察 ───────────────────
  - id: swing_watch
    name: "短线观察"
    description: "RSI/MACD 信号驱动,非持仓但关注中"
    notify_channels: [telegram]
    telegram_chat_ids:
      - "${TELEGRAM_CHAT_ID}"
    notify_on: [signal]                 # 只收信号,不收日报
    symbols:
      - ticker: AMD
        t212_ticker: AMD_US_EQ
        tags: [semiconductor]
      - ticker: TSLA
        t212_ticker: TSLA_US_EQ
        tags: [ev, meme]
        # TSLA 社区噪音大,只采集正向帖子
        community_priority: positive

  # ─────────────────── 指数/ETF ───────────────────
  - id: index_etf
    name: "指数与 ETF"
    description: "宏观参照,不交易"
    notify_channels: [email]
    email_recipients: ["ao@roguelife.de"]
    notify_on: [daily_report]
    t212_community: false               # ETF 没有社区讨论价值
    symbols:
      - ticker: VUAA
        t212_ticker: VUAA_DE_EQ
        tags: [etf, sp500]
      - ticker: QQQ
        t212_ticker: null               # 不在 T212,仅行情
        tags: [etf, nasdaq]

  # ─────────────────── 德股 ───────────────────
  - id: de_stocks
    name: "德股"
    notify_channels: [telegram]
    telegram_chat_ids: ["${TELEGRAM_CHAT_ID}"]
    notify_on: [daily_report, signal, news_shock]
    symbols:
      - ticker: 2DG
        t212_ticker: 2DG_DE_EQ
        tags: [gaming, de]
```

**配置层级与覆盖规则(从低到高优先级)**:

```
defaults (全局默认)
  └─ group 级设置(覆盖 defaults)
       └─ symbol 级设置(覆盖 group)
            └─ extra_email / extra_telegram(追加,不替换)
```

逻辑:
- `notify_channels`、`telegram_chat_ids`、`email_recipients` 每层可覆盖上层
- `extra_email`、`extra_telegram` 是追加到当前解析结果里,不替换
- `notify_on` 控制该 group 收哪些事件类型
- 一个 symbol 可以同时出现在多个 group 里(例如 NVDA 同时在 core_holdings 和某个 swing_watch),各组独立推送、互不干扰

---

## 5. 数据库 Schema

### 5.1 原有表(简写,详见 v1)

```sql
-- prices, news, positions_snapshot, order_intents, signals
-- 与 v1 完全一致,此处省略
```

### 5.2 新增表 — Watchlist / Group / 路由

```sql
-- ─── 分组 ───
CREATE TABLE groups (
  id            TEXT PRIMARY KEY,        -- 'core_holdings'
  name          TEXT NOT NULL,
  description   TEXT,
  config        JSONB NOT NULL           -- 整个 group 的 YAML 解析结果
);

-- ─── watchlist 标的 ───
CREATE TABLE watchlist (
  symbol        TEXT NOT NULL,            -- 'NVDA'
  t212_ticker   TEXT,                     -- 'NVDA_US_EQ',可空
  group_id      TEXT NOT NULL REFERENCES groups(id),
  tags          TEXT[],
  symbol_config JSONB DEFAULT '{}',       -- symbol 级覆盖设置
  active        BOOLEAN DEFAULT true,
  PRIMARY KEY (symbol, group_id)
);

-- ─── 推送路由(系统启动时从 YAML 展开写入,也可 API 动态改) ───
CREATE TABLE notify_routes (
  id            SERIAL PRIMARY KEY,
  group_id      TEXT NOT NULL REFERENCES groups(id),
  symbol        TEXT,                     -- 空 = 组级路由,非空 = symbol 级
  channel       TEXT NOT NULL,            -- 'telegram' | 'email'
  recipient     TEXT NOT NULL,            -- chat_id 或 email 地址
  event_types   TEXT[] NOT NULL,          -- {'daily_report','signal','news_shock'}
  active        BOOLEAN DEFAULT true,
  UNIQUE (group_id, symbol, channel, recipient)
);
```

### 5.3 新增表 — T212 社区帖子

```sql
CREATE TABLE t212_community (
  id            BIGSERIAL PRIMARY KEY,
  topic_id      BIGINT NOT NULL,
  post_id       BIGINT NOT NULL UNIQUE,   -- 社区帖子 ID,去重用
  symbol        TEXT NOT NULL,
  author        TEXT,
  content       TEXT,
  published     TIMESTAMPTZ,
  likes         INT DEFAULT 0,
  sentiment     SMALLINT,                  -- -2..+2,LLM 填
  llm_summary   TEXT,
  fetched_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_t212c_symbol_ts ON t212_community(symbol, published DESC);
```

### 5.4 新增表 — 推送日志

```sql
CREATE TABLE notify_log (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ DEFAULT now(),
  event_type    TEXT NOT NULL,             -- daily_report | signal | news_shock
  group_id      TEXT,
  symbol        TEXT,
  channel       TEXT NOT NULL,             -- telegram | email
  recipient     TEXT NOT NULL,
  status        TEXT NOT NULL,             -- sent | failed | skipped
  error_msg     TEXT,
  payload_hash  TEXT                        -- 防重发:同内容+同收件人 24h 内不重复
);
```

---

## 6. 模块一:T212 账户同步

与 v1 完全一致。`app/t212/client.py` 骨架:

```python
import os, time, httpx

BASE = {
    "demo": "https://demo.trading212.com/api/v0",
    "live": "https://live.trading212.com/api/v0",
}[os.environ.get("T212_ENV", "demo")]

class T212:
    def __init__(self):
        self.h = {"Authorization": os.environ["T212_API_KEY"]}
        self._last_call = {}

    def _throttle(self, path, min_interval=2.0):
        now = time.monotonic()
        wait = self._last_call.get(path, 0) + min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_call[path] = time.monotonic()

    def _get(self, path, **params):
        self._throttle(path)
        for attempt in range(4):
            r = httpx.get(f"{BASE}{path}", headers=self.h,
                          params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited: {path}")

    def positions(self):       return self._get("/equity/positions")
    def cash(self):            return self._get("/equity/account/cash")
    def open_orders(self):     return self._get("/equity/orders")
    def order_history(self, **kw): return self._get("/equity/history/orders", **kw)
    def dividends(self, **kw): return self._get("/equity/history/dividends", **kw)

    def market_order(self, ticker: str, quantity: float):
        self._throttle("/equity/orders/market", 3.0)
        r = httpx.post(f"{BASE}/equity/orders/market", headers=self.h,
                       json={"ticker": ticker, "quantity": quantity}, timeout=30)
        r.raise_for_status()
        return r.json()
```

同步任务(`sync.py`):每 30 分钟拉 positions + cash 写快照;每天收盘后拉 order/dividend 历史增量。

---

## 7. 模块二:T212 社区情绪采集(新增)

Trading212 社区论坛(`community.trading212.com`)是 Discourse 驱动的,提供公开 JSON API。

`app/t212/community.py`:

```python
import httpx, re
from datetime import datetime, timedelta

COMMUNITY_BASE = "https://community.trading212.com"

class T212Community:
    """抓取 T212 Discourse 论坛的公开帖子(无需认证)"""

    def __init__(self):
        self.session = httpx.Client(timeout=30, headers={
            "Accept": "application/json",
            "User-Agent": "StockPilot/1.0 (personal research)"
        })
        self._last_call = 0

    def _throttle(self, interval=2.0):
        import time
        wait = self._last_call + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def search_symbol(self, symbol: str, days: int = 3) -> list[dict]:
        """搜索最近 N 天包含该 symbol 关键词的帖子"""
        self._throttle()
        r = self.session.get(f"{COMMUNITY_BASE}/search.json", params={
            "q": f"{symbol} after:{_days_ago(days)}",
            "order": "latest",
        })
        if r.status_code != 200:
            return []
        data = r.json()
        posts = []
        for post in data.get("posts", []):
            posts.append({
                "topic_id": post["topic_id"],
                "post_id": post["id"],
                "author": post.get("username", ""),
                "content": _strip_html(post.get("blurb", "")),
                "published": post.get("created_at"),
                "likes": post.get("like_count", 0),
            })
        return posts

    def get_topic_posts(self, topic_id: int, limit: int = 20) -> list[dict]:
        """获取某个话题下的回复(用于深挖热门讨论)"""
        self._throttle()
        r = self.session.get(f"{COMMUNITY_BASE}/t/{topic_id}.json")
        if r.status_code != 200:
            return []
        topic = r.json()
        posts = []
        for p in topic.get("post_stream", {}).get("posts", [])[:limit]:
            posts.append({
                "topic_id": topic_id,
                "post_id": p["id"],
                "author": p.get("username", ""),
                "content": _strip_html(p.get("cooked", "")),
                "published": p.get("created_at"),
                "likes": p.get("like_count", 0),
            })
        return posts

def _days_ago(n: int) -> str:
    return (datetime.utcnow() - timedelta(days=n)).strftime("%Y-%m-%d")

def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()
```

**采集策略**:
- 按 watchlist 逐个 symbol 搜索,每天收盘后执行一次
- `community_priority: positive` → LLM 打分后只保留 score ≥ 1 的帖子进日报
- `community_priority: negative` → 只保留 score ≤ -1(用于做空观察)
- `community_priority: all` → 全部保留
- 高赞帖(likes ≥ 5)无论情绪都保留,因为代表社区关注度
- 以 `post_id` 去重,入 `t212_community` 表

---

## 8. 模块三:行情与新闻采集

**行情**(`collectors/prices.py`):

```python
import yfinance as yf

def fetch_daily(symbols: list[str], db):
    df = yf.download(symbols, period="5d", interval="1d",
                     group_by="ticker", auto_adjust=True, threads=False)
    # upsert 到 prices 表

def fetch_intraday(symbols: list[str], db):
    df = yf.download(symbols, period="1d", interval="5m",
                     group_by="ticker", threads=False)
```

- 日线:每天美股收盘后(22:30 CET)全量补 5 天
- 分钟线:盘中每 15 分钟,只拉 watchlist;yfinance 不稳定时降级 Finnhub `/quote`
- 历史回填:首次部署 `period="2y"` 拉两年日线供回测

**新闻**(`collectors/news.py`):
- Finnhub `company-news`(按 watchlist 逐个拉)
- RSS:Reuters / CNBC / SeekingAlpha 宏观源
- 以 `url` 唯一键去重入库
- T212 社区帖子和外部新闻统一进 LLM 情绪打分流程(见 §10)

---

## 9. 模块四:技术指标与信号规则

`analysis/indicators.py` 用 pandas-ta,收盘后对每个 watchlist 标的计算:
RSI(14)、MACD、SMA20/50/200、ATR、布林带、量比。

`analysis/signals.py` 规则引擎:

```python
RULES = [
    ("rsi_oversold",       lambda r: r.rsi < 30 and r.close > r.sma200,    "buy"),
    ("macd_cross_up",      lambda r: r.macd_cross == 1,                     "buy"),
    ("stop_signal",        lambda r: r.close < r.sma50 * 0.97,             "exit"),
    ("news_shock",         lambda r: r.sent_avg <= -1.5 and r.news_cnt >= 3, "alert"),
    # ★ 新增:社区正向爆发(T212 社区近 3 天正向帖 ≥ 5 且均分 ≥ 1.2)
    ("community_bullish",  lambda r: r.comm_pos_cnt >= 5 and r.comm_avg >= 1.2, "alert"),
]
```

信号只入库 + 推送,**不直接触发下单**。命中 buy/exit 的信号会生成 `order_intent`(pending),等待人工确认。

---

## 10. 模块五:LLM 情绪分析与日报(Dify)

### Workflow A — 新闻 + 社区统一情绪打分

输入增加了 `community_posts` 字段:

```json
{
  "symbol": "NVDA",
  "news": [
    {"url": "https://…", "title": "...", "summary": "...", "source": "finnhub"}
  ],
  "community_posts": [
    {"post_id": 12345, "author": "user123", "content": "...", "likes": 8, "source": "t212_community"}
  ]
}
```

Prompt:

```
你是金融信息分析员。对以下关于 {symbol} 的新闻和社区帖子逐条打分并汇总。
区分"新闻"(专业媒体)和"社区"(散户讨论),权重不同:新闻权重 1.0,社区权重 0.5。
高赞帖(likes ≥ 5)权重提升到 0.8。

只输出 JSON:
{
  "news_items":      [{"url": "...", "score": -2到2, "reason": "一句话"}],
  "community_items": [{"post_id": N, "score": -2到2, "reason": "一句话"}],
  "news_overall":    -2到2 (加权),
  "community_overall": -2到2 (加权),
  "combined_overall": -2到2,
  "summary":         "不超过80字",
  "catalysts":       ["财报"|"评级"|"社区热议"|"产品"|"监管"...],
  "community_signal": "bullish" | "bearish" | "neutral" | "mixed"
}
```

### Workflow B — 每日报告生成(按组)

不再生成单一全局日报,而是**按 group 生成**。每个 group 只包含该组 symbols 的信息。

输入:

```json
{
  "group_id": "core_holdings",
  "group_name": "核心持仓",
  "symbols_data": [
    {
      "symbol": "NVDA",
      "price_change_pct": 3.2,
      "rsi": 68.2,
      "news_sentiment": 0.8,
      "community_sentiment": 1.2,
      "community_signal": "bullish",
      "top_community_post": "AI 数据中心订单排到 2027...",
      "signals": ["macd_cross_up"],
      "ppl": 201.30,
      "ppl_pct": 19.8
    }
  ],
  "account_summary": { "total": 12847, "cash": 2150, "daily_pnl": -23.40 },
  "language": "zh"
}
```

输出 Markdown:
① 组概览 ② 持仓异动 Top3 ③ 信号列表 ④ 新闻雷达 ⑤ **社区风向**(新增) ⑥ 明日关注

---

## 11. 模块六:多通道推送与路由(核心新增)

### 11.1 路由引擎 `notify/router.py`

```python
from db import get_session
from models import NotifyRoute, NotifyLog
import hashlib, json

class NotifyRouter:
    """根据事件类型 + group + symbol 查路由表,分发到各通道"""

    def __init__(self, telegram_sender, email_sender):
        self.tg = telegram_sender
        self.email = email_sender

    async def dispatch(self, event_type: str, symbol: str,
                       group_id: str, payload: dict):
        """
        event_type: 'daily_report' | 'signal' | 'news_shock'
        payload: {"subject": "...", "body_md": "...", "body_html": "..."}
        """
        routes = self._resolve_routes(event_type, symbol, group_id)
        for route in routes:
            # 防重发:24h 内同内容+同收件人不重复
            h = _payload_hash(route.recipient, payload)
            if self._already_sent(h):
                self._log(route, "skipped", "duplicate within 24h")
                continue
            try:
                if route.channel == "telegram":
                    await self.tg.send(route.recipient, payload["body_md"])
                elif route.channel == "email":
                    await self.email.send(
                        to=route.recipient,
                        subject=payload.get("subject", "StockPilot 通知"),
                        body_html=payload.get("body_html", ""),
                    )
                self._log(route, "sent")
            except Exception as e:
                self._log(route, "failed", str(e))

    def _resolve_routes(self, event_type, symbol, group_id):
        """查 notify_routes 表,返回匹配的路由列表"""
        with get_session() as s:
            # 先查 symbol 级路由,再查 group 级(symbol=NULL),合并去重
            routes = s.query(NotifyRoute).filter(
                NotifyRoute.group_id == group_id,
                NotifyRoute.active == True,
                NotifyRoute.event_types.contains([event_type]),
            ).filter(
                (NotifyRoute.symbol == symbol) | (NotifyRoute.symbol == None)
            ).all()
            # 去重:同 channel+recipient 只保留一条(symbol 级优先)
            seen = set()
            result = []
            for r in sorted(routes, key=lambda x: (x.symbol is None)):
                key = (r.channel, r.recipient)
                if key not in seen:
                    seen.add(key)
                    result.append(r)
            return result

    def _log(self, route, status, error=None):
        # 写 notify_log 表
        ...

    def _already_sent(self, payload_hash) -> bool:
        # 查 notify_log,24h 内有同 hash 的 sent 记录则 True
        ...

def _payload_hash(recipient, payload):
    raw = f"{recipient}:{json.dumps(payload, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### 11.2 Telegram 多目标推送 `notify/telegram.py`

```python
from telegram import Bot
from telegram.constants import ParseMode
import os

class TelegramSender:
    def __init__(self):
        self.bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])

    async def send(self, chat_id: str, markdown_text: str,
                   reply_markup=None):
        await self.bot.send_message(
            chat_id=chat_id,
            text=markdown_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )

    async def send_signal_card(self, chat_id: str, signal: dict,
                               intent_id: str):
        """带确认按钮的信号卡片"""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        text = (
            f"📈 *{signal['symbol']}* · {signal['rule']}\n"
            f"方向: {signal['direction']} · 强度: {signal['strength']:.2f}\n"
            f"建议: €{signal['order_value']:.0f} ≈ {signal['quantity']:.2f} 股\n"
            f"`intent: {intent_id[:8]}…{intent_id[-4:]}`"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认下单", callback_data=f"confirm:{intent_id}"),
            InlineKeyboardButton("❌ 忽略", callback_data=f"skip:{intent_id}"),
        ]])
        await self.send(chat_id, text, reply_markup=kb)
```

### 11.3 Email 推送 `notify/email.py`

```python
import smtplib, os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailSender:
    def __init__(self):
        self.host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ["SMTP_USER"]
        self.password = os.environ["SMTP_PASSWORD"]
        self.from_addr = os.environ.get("SMTP_FROM", self.user)

    async def send(self, to: str, subject: str, body_html: str):
        msg = MIMEMultipart("alternative")
        msg["From"] = f"StockPilot <{self.from_addr}>"
        msg["To"] = to
        msg["Subject"] = subject
        # 纯文本 fallback
        from html import unescape
        import re
        plain = re.sub(r"<[^>]+>", "", unescape(body_html))
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        # 同步发送(在 executor 线程池里跑)
        with smtplib.SMTP(self.host, self.port) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.sendmail(self.from_addr, [to], msg.as_string())
```

SMTP 配置建议:
- Gmail:开 App Password,免费够用(每天 500 封)
- 自建:你服务器已有域名,可用 Mailgun / Resend 免费档(100 封/天)
- `.env` 里加:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # App Password
SMTP_FROM=stockpilot@roguelife.de
```

---

## 12. 模块七:管理 API `api/watchlist.py`

FastAPI 路由,提供 CRUD 操作,不用写前端——直接 curl 或用 Grafana 的 API 面板。

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["watchlist"])

# ─── Group CRUD ───
class GroupCreate(BaseModel):
    id: str                            # 'my_new_group'
    name: str
    description: str = ""
    notify_channels: list[str] = ["telegram"]
    telegram_chat_ids: list[str] = []
    email_recipients: list[str] = []
    notify_on: list[str] = ["daily_report", "signal"]

@router.get("/groups")
async def list_groups(db=Depends(get_db)):
    return db.query(Group).all()

@router.post("/groups")
async def create_group(g: GroupCreate, db=Depends(get_db)):
    # 建组 + 展开 notify_routes
    ...

@router.put("/groups/{group_id}")
async def update_group(group_id: str, g: GroupCreate, db=Depends(get_db)):
    # 更新组 + 重建 notify_routes
    ...

@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, db=Depends(get_db)):
    ...

# ─── Symbol CRUD ───
class SymbolAdd(BaseModel):
    symbol: str                        # 'NVDA'
    t212_ticker: str | None = None     # 'NVDA_US_EQ'
    tags: list[str] = []
    extra_email: list[str] = []
    extra_telegram: list[str] = []

@router.post("/groups/{group_id}/symbols")
async def add_symbol(group_id: str, s: SymbolAdd, db=Depends(get_db)):
    # 加标的 + 展开 symbol 级 notify_routes
    ...

@router.delete("/groups/{group_id}/symbols/{symbol}")
async def remove_symbol(group_id: str, symbol: str, db=Depends(get_db)):
    ...

# ─── Recipient 管理 ───
class RecipientUpdate(BaseModel):
    channel: str                       # 'telegram' | 'email'
    recipients: list[str]              # chat_ids 或 email 地址列表
    event_types: list[str] = ["daily_report", "signal"]

@router.put("/groups/{group_id}/recipients")
async def update_recipients(group_id: str, r: RecipientUpdate,
                            db=Depends(get_db)):
    # 重建该 group + channel 的 notify_routes
    ...

# ─── 查询 ───
@router.get("/routes")
async def list_routes(group_id: str = None, symbol: str = None,
                      db=Depends(get_db)):
    """查看当前所有生效的推送路由,可按 group/symbol 过滤"""
    ...

@router.get("/notify-log")
async def get_notify_log(hours: int = 24, db=Depends(get_db)):
    """最近 N 小时的推送日志"""
    ...

# ─── 配置同步 ───
@router.post("/sync-yaml")
async def sync_from_yaml(db=Depends(get_db)):
    """重新从 watchlist.yaml 加载,覆盖数据库(幂等)"""
    ...

@router.get("/export-yaml")
async def export_to_yaml(db=Depends(get_db)):
    """把数据库当前状态导出为 YAML"""
    ...
```

使用示例:

```bash
# 新建一个组
curl -X POST http://localhost:8100/api/v1/groups \
  -H 'Content-Type: application/json' \
  -d '{"id":"crypto_watch","name":"加密观察","notify_channels":["telegram"],"telegram_chat_ids":["-100999"],"notify_on":["signal"]}'

# 往组里加标的
curl -X POST http://localhost:8100/api/v1/groups/crypto_watch/symbols \
  -d '{"symbol":"COIN","t212_ticker":"COIN_US_EQ","tags":["crypto"]}'

# 给 NVDA 单独加一个邮件收件人
curl -X POST http://localhost:8100/api/v1/groups/core_holdings/symbols \
  -d '{"symbol":"NVDA","extra_email":["friend@example.com"]}'

# 查看所有路由
curl http://localhost:8100/api/v1/routes

# 查看推送日志
curl http://localhost:8100/api/v1/notify-log?hours=48
```

---

## 13. 推送内容模板

### 13.1 Telegram 早报(按 group)

```
📋 核心持仓 · 2026-06-11 周四

① 账户:€12,847 (+2.5% / 30d)
   现金 €2,150 · 今日财报:无持仓相关

② 持仓异动
   NVDA  +3.2%  数据中心订单超预期
   AAPL  −1.8%  欧盟反垄断听证临近
   MSFT  +0.6%  无重大新闻

③ 信号
   • AAPL rsi_oversold → 待确认单已推送

④ 新闻雷达
   NVDA: 出口管制传闻发酵中 (情绪 -1.5)

⑤ 社区风向                          ← 新增
   NVDA: 🟢 看多 (8 帖,均分 +1.4)
   热帖: "AI datacenter orders booked through 2027" 👍12
   AAPL: ⚪ 中性 (3 帖,均分 +0.2)

⑥ 明日:关注 CPI 数据 14:30 ET
```

### 13.2 Email 早报(HTML 版,同内容)

```html
<div style="font-family:monospace;max-width:600px;margin:0 auto">
  <h2 style="border-bottom:2px solid #E8A33D;padding-bottom:8px">
    📋 核心持仓 · 2026-06-11
  </h2>
  <table style="width:100%;border-collapse:collapse">
    <tr>
      <td style="color:#888;padding:4px 0">NVDA</td>
      <td style="text-align:right;color:#3DD68C">+3.2%</td>
      <td style="color:#888;padding-left:12px">数据中心订单超预期</td>
    </tr>
    <!-- ... -->
  </table>
  <h3>社区风向</h3>
  <p>NVDA: 🟢 看多 · 8 帖 · 均分 +1.4<br>
  <em>"AI datacenter orders booked through 2027"</em> 👍12</p>
  <hr>
  <p style="font-size:12px;color:#888">
    StockPilot · 本邮件由系统自动生成,不构成投资建议
  </p>
</div>
```

### 13.3 信号推送(Telegram,按 group 路由)

```
📈 信号触发 · AAPL_US_EQ
组: 核心持仓
规则: rsi_oversold (RSI 28.4, 价格站上 SMA200)
新闻情绪: +0.6 · 社区情绪: +0.2 (中性)

建议: 买入 €150.00 ≈ 0.79 股 @ 189.92
intent: 7f3a…c21d · 30 分钟内有效

[✅ 确认下单] [❌ 忽略]
```

### 13.4 news_shock 告警(推送到该 symbol 所在的所有 group)

```
⚠️ NVDA 新闻异动
情绪骤降: -1.5 (近 3 日均值)
社区: 🔴 3 条看空帖涌入

触发原因:
• US weighs new chip export restrictions (Finnhub, -2)
• Trade ban fears escalate (RSS, -2)
• 社区: "Selling my NVDA position ahead of announcement" 👍6

该标的属于组: 核心持仓, 短线观察
```

---

## 14. 完整推送流程(数据流图)

```
收盘 22:40
  │
  ├─ 日线采集 + 指标计算 + 信号评估
  │
  ▼
22:50  T212 社区采集(按 watchlist 逐 symbol)
  │
  ▼
23:00  Dify Workflow A(新闻 + 社区统一打分)
  │    写回 news.sentiment + t212_community.sentiment
  │
  ▼
23:10  信号评估(含 community_bullish 规则)
  │    命中 → 写 order_intents + signals
  │
  ▼
23:15  即时推送(信号/news_shock)
  │    router.dispatch()
  │    ├─ 查 notify_routes(group + symbol + event_type)
  │    ├─ Telegram: 逐 chat_id 推送卡片
  │    └─ Email: 逐收件人发送
  │
  ▼
08:00 次日早报
  │    按 group 逐组生成(Dify Workflow B)
  │    router.dispatch(event_type='daily_report')
  │    ├─ core_holdings → TG(你+群) + Email(你+朋友)
  │    ├─ swing_watch   → TG(你)
  │    ├─ index_etf     → Email(你)
  │    └─ de_stocks     → TG(你)
```

---

## 15. docker-compose.yml

```yaml
services:
  tsdb:
    image: timescale/timescaledb:latest-pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: stockpilot
      POSTGRES_USER: sp
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - tsdb_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits: { memory: 1g }
    command: postgres -c shared_buffers=256MB -c max_connections=50

  grafana:
    image: grafana/grafana-oss:latest
    restart: unless-stopped
    ports: ["3001:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    deploy:
      resources:
        limits: { memory: 300m }

  app:
    build: ./app
    restart: unless-stopped
    env_file: .env
    depends_on: [tsdb]
    ports: ["8100:8000"]
    volumes:
      - ./config:/app/config:ro
    deploy:
      resources:
        limits: { memory: 700m }

volumes:
  tsdb_data:
  grafana_data:
```

`.env` 模板(v2 新增项标 ★):

```ini
DB_PASSWORD=...
GRAFANA_PASSWORD=...
T212_API_KEY=...
T212_ENV=demo
FINNHUB_TOKEN=...
DIFY_API_KEY=...
DIFY_BASE_URL=http://<dify容器或宿主IP>/v1
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...             # 你的主 chat(也在 YAML 里引用)
# ★ SMTP 邮件
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
SMTP_FROM=stockpilot@roguelife.de
# 风控参数
RISK_MAX_ORDER_EUR=200
RISK_MAX_POSITION_PCT=15
RISK_DAILY_LOSS_LIMIT_EUR=100
```

---

## 16. Grafana 看板(更新)

四块 Dashboard:

1. **账户复盘**:总资产曲线、持仓盈亏、月度已实现、分红、现金占比
2. **市场雷达**:价格走势 + 信号 annotation、RSI/MACD、新闻情绪热力图、**社区情绪热力图**(新增)
3. **推送监控**(新增):notify_log 可视化 — 每个 group × channel 的成功/失败计数、最近推送时间线、收件人覆盖表
4. **系统健康**:采集任务状态、API 调用统计、429 次数

---

## 17. Telegram 确认下单(核心安全设计)

与 v1 一致,补充 group 路由逻辑:

```
信号触发 → 写 order_intents
        → router 查该 symbol 所在所有 group 中 event_type='signal' 的路由
        → 逐 chat_id 推送确认卡片(只有你自己的 chat_id 有确认按钮)
        → 群组的 chat_id 只收"只读通知"(无按钮)
点击确认 → webhook → 风控 → 幂等检查 → market_order → 回执
超时 30min → expired
```

安全:确认按钮的 callback 里校验 `user_id == ADMIN_USER_ID`,防止群里其他人点。

---

## 18. 回测(vectorbt)

与 v1 一致,略。

---

## 19. 硬风控

与 v1 一致,略。

---

## 20. 调度表(v2 更新)

| 时间(欧洲) | 任务 |
|---|---|
| 每 30 min | T212 持仓/现金快照 |
| 盘中(15:30–22:00)每 15 min | watchlist 分钟线 + Finnhub 快讯 |
| 22:40 | 日线采集 + 指标计算 |
| 22:50 | ★ T212 社区帖子采集(按 watchlist) |
| 23:00 | Dify 情绪批处理(新闻 + 社区统一) |
| 23:10 | 信号评估 + 即时推送(signal / news_shock) |
| 08:00 | ★ 按 group 逐组生成早报 + 分发 |
| 周日 03:00 | pg_dump 备份 |

---

## 21. 落地计划(8 周)

**第 1 周 — 地基**:compose 起 tsdb + grafana;建全部 schema(含新表);T212 demo API key;跑通 positions/cash 同步;Grafana 账户看板
**第 2 周 — 数据**:yfinance 日线 + 2 年回填;Finnhub 新闻;watchlist.yaml 加载逻辑;Telegram bot
**第 3 周 — 社区采集**:T212 community 爬虫;入库;手动验证数据质量
**第 4 周 — 分析**:pandas-ta 指标;Dify Workflow A(含社区);市场雷达看板(含社区热力)
**第 5 周 — 推送系统**:notify_routes 展开逻辑;router;Email sender;notify_log;推送监控看板
**第 6 周 — 日报**:Dify Workflow B(按组);完整推送闭环;信号规则引擎(只推送不下单)
**第 7 周 — 回测**:vectorbt 跑通;样本外验证;管理 API 完善
**第 8 周 — 交易闭环(demo)**:确认下单 + 风控 + 幂等;demo 完整跑通
**之后 ≥ 4 周**:demo 实跑一个月 → 切 live,首月 €50 上限

---

## 22. 风险与免责

- 回测好看 ≠ 实盘赚钱;新闻/社区情绪信号极易过拟合
- T212 社区抓取依赖 Discourse 公开 API,格式可能变化,需要维护
- T212 API 仍在 beta:端点不幂等、条款可能变化
- LLM 对社区散户帖子的分析噪音更大,权重必须低于专业新闻
- 邮件推送可能进垃圾箱,首次配置后让收件人标记"非垃圾"
- 仓位控制 > 信号质量;熔断参数宁紧勿松
- 本方案完全分析短线价值，提供定制投资建议

---

## 附:requirements.txt

```
fastapi
uvicorn[standard]
httpx
apscheduler
sqlalchemy
psycopg[binary]
alembic
yfinance
pandas
pandas-ta
vectorbt
feedparser
python-telegram-bot
pyyaml
jinja2
aiosmtplib
```
