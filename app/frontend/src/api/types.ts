// ── 后端 API 响应类型 ────────────────────────────────────────────────────────

export interface HealthResponse {
  status: 'ok' | 'degraded'
  db: boolean
  scheduler: boolean
  integrations: Record<string, boolean>
}

export interface AccountSnapshot {
  ts: string
  total: number | null
  free_cash: number | null
  ppl: number | null
}

export interface SummaryPosition {
  ticker: string
  quantity: number | null
  avg_price: number | null
  current_price: number | null
  ppl: number | null
}

export interface SignalItem {
  ts: string
  symbol: string
  rule: string
  direction: string
  strength: number | null
}

export interface PendingIntent {
  id: string
  symbol: string
  side: string
  value: number | null
  expires_at: string | null
}

export interface DashboardSummary {
  account: AccountSnapshot | null
  positions: SummaryPosition[]
  signals_24h: SignalItem[]
  pending_intents: PendingIntent[]
}

// ── 任务 ──
export interface JobRun {
  job: string
  started_at: string
  finished_at: string | null
  status: 'ok' | 'running' | 'failed' | 'skipped' | string
  detail: string | null
}

export interface JobsResponse {
  available: string[]
  recent_runs: JobRun[]
}

export interface ScheduledJob {
  id: string
  next_run: string | null
  trigger: string
}

export interface JobScheduleResponse {
  jobs: ScheduledJob[]
  timezone: string
}

// ── 分组 ──
export interface GroupConfig {
  symbols?: Array<{
    ticker?: string
    symbol?: string
    t212_ticker?: string
    tags?: string[]
  }>
  telegram_chat_ids?: string[]
  email_recipients?: string[]
  notify_channels?: string[]
  notify_on?: string[]
  [k: string]: unknown
}

export interface Group {
  id: string
  name: string
  description: string | null
  config: GroupConfig | null
  symbol_count?: number
}

export interface GroupSymbol {
  symbol: string
  t212_ticker: string | null
  tags: string[]
  symbol_config: Record<string, unknown>
}

export interface GroupDetail {
  id: string
  name: string
  description: string | null
  config: GroupConfig | null
  symbols: GroupSymbol[]
}

// ── 路由 / 日志 ──
export interface NotifyRoute {
  id: number
  group_id: string
  symbol: string | null
  channel: string
  recipient: string
  event_types: string[]
}

export interface NotifyLog {
  ts: string
  event_type: string
  group_id: string | null
  symbol: string | null
  channel: string
  recipient: string
  status: 'sent' | 'failed' | 'skipped' | string
  error_msg: string | null
}

// ── T212 ──
export interface T212Position {
  ticker: string
  name: string
  isin: string
  currency: string
  quantity: number
  quantityAvailableForTrading: number
  averagePrice: number | null
  currentPrice: number | null
  ppl: number | null
  totalCost: number | null
  currentValue: number | null
  pnlCurrency: string
}

export interface T212Instrument {
  ticker: string
  name: string
  shortName?: string
  type?: string
  currencyCode?: string
  currency?: string
  isin?: string
  // 持仓视图下会混入持仓字段
  quantity?: number
  currentPrice?: number | null
  ppl?: number | null
  pnlCurrency?: string
  averagePrice?: number | null
}

export interface WatchlistItem {
  ticker: string
  name: string
  added_at?: string
}

export interface ActivityNews {
  title: string
  sentiment: number | null
  published: string
  source: string
  url: string
}

export interface ActivityPost {
  content: string
  author: string
  published: string
  likes: number
  sentiment: number | null
}

export interface InstrumentActivity {
  symbol: string
  ticker: string
  tracking_groups: string[]
  news: ActivityNews[]
  community: ActivityPost[]
}

export interface OpenOrder {
  id: number
  ticker: string
  type?: string
  orderType?: string
  quantity: number
  limitPrice?: number
  stopPrice?: number
  status?: string
  timeValidity?: string
  side?: string
}

export type TimeValidity = 'DAY' | 'GOOD_TILL_CANCEL'
export type OrderSide = 'buy' | 'sell'
export type OrderKind = 'market' | 'limit' | 'stop'

// ── 标的详情（技术指标 + 情绪） ──
export interface IndicatorDetail {
  symbol: string
  ts: string | null
  rsi: number | null
  macd_cross: number | null
  sma20: number | null
  sma50: number | null
  sma200: number | null
  atr: number | null
  vol_ratio: number | null
}

export interface SentimentAgg {
  sent_avg: number | null
  comm_avg: number | null
  comm_cnt: number
  comm_pos_cnt: number
  comm_neg_cnt: number
  news_cnt: number
}

export interface SentimentNewsItem {
  title: string
  source: string
  url: string
  score: number | null
  reason: string | null
}

export interface SentimentDetail {
  symbol: string
  aggregates: SentimentAgg
  label: string | null
  news: SentimentNewsItem[]
}

export interface SymDetail {
  ind: IndicatorDetail | null
  sent: SentimentDetail | null
}

// ── 实时行情 ──
export interface RTQuote {
  bid: number | null
  ask: number | null
  last: number | null
  open: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  change_pct: number | null
  source: string | null
}

export interface RTTrade {
  p: number
  v: number
  t: number
  d: number
}

// ── 量化策略 ──
export interface QuantStrategyStatus {
  symbol: string
  t212_ticker: string
  running: boolean
  env: string
  params: {
    buy_mode: 'ind' | 'market'
    rsi_buy: number
    stop_loss: number
    profit_pct: number
    budget_ratio: number
    budget_eur: number
    sell_ratio: number
    interval: number
    max_trades_day: number
    [key: string]: unknown
  }
  holding: boolean
  quantity: number
  avg_price: number
  last_price: number
  gain_pct: number | null
  indicators: { rsi: number; macd_diff: number; ticks: number } | null
  trades_today: number
  total_trades: number
  total_pnl: number
  last_check: string | null
  last_action: string | null
  error: string | null
}

export interface QuantTradeRecord {
  ts: string
  symbol: string
  side: 'buy' | 'sell'
  reason: string
  quantity: number
  price: number
  pnl: number | null
  order_id: string
}
