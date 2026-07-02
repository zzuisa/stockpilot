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
  progress: string | null
  detail: string | null
}

// 数据更新流（应用内通知中心）
export interface DataUpdate {
  id: number
  ts: string
  kind: 'news' | 'sentiment' | 'signal' | 'trade' | string
  symbol: string | null
  title: string
  detail: Record<string, unknown> | null
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
export interface GroupRecipient {
  channel: 'telegram' | 'email'
  recipient: string
  events: string[]
}

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
  recipients?: GroupRecipient[]
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
export interface T212Cash {
  free: number | null
  total: number | null
  invested: number | null
  ppl: number | null
  result: number | null
  blocked: number | null
}

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
  news_tier_avg: number | null   // 平均来源等级(越接近1越权威)
}

export interface SentimentNewsItem {
  title: string
  source: string
  source_name: string | null
  source_tier: number | null   // 1=一线权威 2=主流 3=PR/未知
  relevance: number | null
  quality: number | null       // 0..1 综合质量分
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
    buy_mode: 'ind' | 'market' | 'turning'
    rsi_buy: number
    stop_loss: number
    profit_pct: number
    budget_ratio: number
    budget_eur: number
    sell_ratio: number
    interval: number
    max_trades_day: number
    currency?: 'USD' | 'EUR'
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
  // 本次开启以来收益统计
  started_at?: string
  realized_pnl?: number
  wins?: number
  losses?: number
  win_rate?: number | null
  roi_pct?: number | null
  last_check: string | null
  last_action: string | null
  last_explain?: string | null     // 最近一次动作的决策解释(规则/LLM)
  error: string | null
  waiting?: string | null
  turn_signal?: string | null      // 拐点信号 buy|sell|hold (turning 模式)
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

// ── T212 账户 ──
export interface T212Account {
  id: number
  name: string
  env: 'demo' | 'live'
  is_active: boolean
  created_at: string
  api_key_hint: string
  has_secret: boolean    // 是否已配置 API Secret
}

// ── 新闻 LLM ──
export interface NewsItem {
  id: number
  symbol: string | null
  source: string
  source_name: string | null
  source_tier: number | null
  relevance: number | null
  quality: number | null
  title: string | null
  url: string
  published: string | null
  fetched_at: string | null
  sentiment: number | null
  llm_reason: string | null
}

export interface NewsStats {
  hours: number
  total: number
  scored: number
  unscored: number
  by_sentiment: Record<string, number>
  avg: number | null
  last_scored_ts: string | null
}

export interface AdviceThesis {
  dim: string
  point: string
  support: string
}
export interface AdviceResult {
  stance?: string
  is_near_low?: boolean
  entry?: string
  exit?: string
  horizon?: string
  thesis?: AdviceThesis[]
  confidence?: number
  caveats?: string
  is_realtime?: boolean
  price?: number | null
  change_pct?: number | null
  window?: { start: string; end: string }
  created_at?: string
}

export interface AdviceHistoryItem {
  id: number
  ts: string | null
  is_realtime: boolean
  stance: string | null
  window: { start: string | null; end: string | null }
  result: AdviceResult
  reasoning: string | null
}

export interface NewsBrief {
  id: number
  symbol: string
  ts: string | null
  window_hours: number | null
  headline: string | null
  sentiment: string | null
  judgment: string | null
  summary_md: string | null
  watch_points: string | null
  item_count: number
  tokens: number
  pushed: boolean
}

// ── 财报 + 研究分析 ──
export interface EarningsReport {
  id: number
  symbol: string
  period: string
  downloaded_at: string
  filename: string
  size: number
  source: string
}

export type BubbleLevel = 'normal' | 'slight' | 'moderate' | 'severe' | 'extreme'

export interface BubbleKeyFactor {
  factor: string
  signal: string
  detail: string
}

export interface BubbleAnalysis {
  bubble_level: BubbleLevel
  bubble_pct: number
  fundamental_value: number
  summary: string
  key_factors: BubbleKeyFactor[]
  risk_warning: string
  tokens: number
  report_period: string
}

export interface KeyLevel {
  support1: number
  support2: number
  resistance1: number
  resistance2: number
}

export interface InvestmentStrategy {
  recommendation: 'buy' | 'hold' | 'reduce' | 'sell'
  confidence: number
  target_price_low: number
  target_price_high: number
  stop_loss: number
  holding_period: string
  trend_phase: string
  trend_strength: string
  technical_signal: string
  rsi_status: string
  macd_status: string
  summary: string
  catalysts: Array<{ type: string; description: string }>
  key_levels: KeyLevel
  risk_factors: string[]
  tokens: number
  current_price: number
}

export interface IndicatorPoint {
  ts: string
  close: number | null
  rsi: number | null
  macd: number | null
  macd_signal: number | null
  macd_hist: number | null
}

export interface ResearchLatest {
  history: IndicatorPoint[]
  bubble?: {
    ts: string
    bubble_level: BubbleLevel
    bubble_pct: number
    tokens: number
    report_period: string | null
    data: BubbleAnalysis
  }
  strategy?: {
    ts: string
    tokens: number
    data: InvestmentStrategy
  }
}

// 按需数据补全(研究分析前置)的分步结果
export interface EnsureDataStep {
  name: string
  status: 'done' | 'failed'
  detail: string
}
export interface EnsureDataResult {
  steps: EnsureDataStep[]
  ready: boolean
}

// ── 盘前日报 (Daily Brief) ──
export interface OptionGammaStrike {
  strike: number
  gex: number
  call_oi: number
  put_oi: number
}
export interface OptionMetrics {
  spot: number
  gex: number
  pcr_oi: number | null
  pcr_vol: number | null
  iv_atm: number | null
  call_wall: number | null
  put_wall: number | null
  expected_move_pct: number | null
  gamma_by_strike: OptionGammaStrike[]
  expiries: string[]
}
export interface BriefChips {
  pattern: string
  momentum: string
  signal: string
  money_flow: string
  vol_label: string
}
export interface BriefComment {
  core_take?: string
  trend_comment?: string
  options_comment?: string
  levels_comment?: string
}
export interface TrendData {
  dates: string[]
  close: number[]
  fast: number[]
  slow: number[]
  band_upper: number[]
  band_lower: number[]
  regime: string[]
  spread: number[]
  weekly_spread: number[]
  money_flow_usd: number
  relative_volume: number
  trend_label: string
}
export interface BriefHistoryPoint {
  ts: string
  rsi: number | null
  macd: number | null
  macd_hist: number | null
  close: number | null
}
export interface DailyBriefData {
  ok?: boolean
  symbol: string
  ts: string
  price: number
  prev_close: number
  change_pct: number
  chips: BriefChips
  indicators: IndicatorDetail & Record<string, number | null>
  history: BriefHistoryPoint[]
  price_history: { ts: string; close: number }[]
  trend: TrendData | null
  sentiment: SentimentAgg
  options: OptionMetrics | null
  comment: BriefComment
}

export interface LlmStatus {
  enabled: boolean
  running: boolean
  phase: string                // idle | scoring | sleeping
  progress: string
  interval: number
  scored_total: number
  tokens_total: number
  tokens_last: number
  scored_last: number
  calls: number
  last_batch_at: string | null
  started_at: string | null
  error: string | null
  model: string | null
}

// ── 统一交易历史 / 大屏 ──
export interface TradeLog {
  ts: string
  source: 'manual' | 'quant'
  symbol: string | null
  t212_ticker: string | null
  side: 'buy' | 'sell'
  order_type: string | null
  quantity: number | null
  price: number | null
  value_eur: number | null
  currency: string | null
  pnl: number | null
  reason: string | null
  status: string | null
  order_id: string | null
  env: string | null
}

// 近期真实成交（来自 T212 订单历史，非挂单/估算）
export interface RecentFill {
  ts: string
  ticker: string
  symbol: string | null
  side: 'buy' | 'sell'
  quantity: number
  price: number
  value_eur: number
  pnl: number | null
  order_type: string
  order_id: string
  reason: string | null
}

export interface EquityPoint {
  ts: string
  total: number | null
  free_cash: number | null
  invested: number | null
  ppl: number | null
  result: number | null
}

export interface TradeStats {
  trade_count: number
  win: number
  loss: number
  win_rate: number
  realized_pnl: number
  today_pnl: number
  total_fills: number
  by_day: Array<{ day: string; pnl: number }>
}

// ── ETF 全天候回测 (All Weather Lab) ──
export interface BacktestTier {
  dd: number
  amount: number
}
export interface BacktestOpp {
  symbol: string
  tiers: BacktestTier[]
}
export interface BacktestConfig {
  weights?: Record<string, number>
  monthly_dca: number
  initial: number
  rebalance_months: number
  rebalance_set?: number[]
  opportunity?: BacktestOpp[]
  opp_cap?: number | null
  benchmark?: string
  start?: string | null
  end?: string | null
}
export interface BacktestKpi {
  cagr: number
  vol: number
  maxdd: number
  sharpe: number
  final: number
  dca_total: number
  opp_total: number
  total_invested: number
  net_profit: number
}
export interface BacktestComparisonRow {
  name: string
  type: string
  cagr: number
  vol: number
  maxdd: number
  sharpe: number
  final: number
  total_invested: number
  net_profit: number
  dca_total: number
  opp_total: number
}
export interface BacktestAssetRow {
  symbol: string
  label: string
  target_weight: number
  net_invested: number
  final_value: number
  profit: number
  max_drift: number
}
export interface BacktestOppEvent {
  date: string
  symbol: string
  tier: number
  drawdown_pct: number
  amount: number
}
export interface BacktestRebalance {
  date: string
  trades: Array<{ symbol: string; delta_usd: number }>
}
export interface BacktestSeries {
  dates: string[]
  equity: number[]
  drawdown: number[]
  cumulative: number[]
  annualized: Array<number | null>
}
export interface BacktestResult {
  error?: string
  available?: string[]
  effective_range: [string, string]
  primary_rebalance: number
  kpi: BacktestKpi
  comparison: BacktestComparisonRow[]
  per_asset: BacktestAssetRow[]
  max_drift_overall: number
  annual_returns: Array<{ year: number; return_pct: number }>
  opportunities: BacktestOppEvent[]
  rebalances: BacktestRebalance[]
  series: BacktestSeries
  config: Record<string, unknown>
}
export interface BacktestDataStatus {
  symbols: Array<{ symbol: string; label: string; rows: number; start: string | null; end: string | null }>
  ready: number
  total: number
}
