import { API_BASE, http, rootHttp } from './client'
import type {
  BacktestConfig,
  BacktestResult,
  BacktestDataStatus,
  ActivityNews,
  BubbleAnalysis,
  DashboardSummary,
  DataUpdate,
  BriefComment,
  DailyBriefData,
  EarningsReport,
  EnsureDataResult,
  EquityPoint,
  Group,
  GroupDetail,
  GroupRecipient,
  HealthResponse,
  IndicatorDetail,
  InstrumentActivity,
  InvestmentStrategy,
  JobScheduleResponse,
  JobsResponse,
  NotifyLog,
  LlmStatus,
  NewsBrief,
  NewsItem,
  NewsStats,
  NotifyRoute,
  OpenOrder,
  OrderSide,
  QuantStrategyStatus,
  QuantTradeRecord,
  RecentFill,
  ResearchLatest,
  RTQuote,
  SentimentDetail,
  T212Account,
  T212Cash,
  T212Instrument,
  T212Position,
  TimeValidity,
  TradeLog,
  TradeStats,
  WatchlistItem,
} from './types'

// ── 系统 / 看板 ──────────────────────────────────────────────────────────────
export const systemApi = {
  health: () => rootHttp.get<HealthResponse>('/health').then((r) => r.data),
  summary: () => http.get<DashboardSummary>('/dashboard/summary').then((r) => r.data),
}

// ── 任务 ──────────────────────────────────────────────────────────────────────
export const jobsApi = {
  list: () => http.get<JobsResponse>('/jobs').then((r) => r.data),
  schedule: () => http.get<JobScheduleResponse>('/jobs/schedule').then((r) => r.data),
  run: (name: string) =>
    http.post<{ job: string; status: string }>(`/jobs/${name}/run`).then((r) => r.data),
}

// ── 分组 ──────────────────────────────────────────────────────────────────────
export const groupsApi = {
  list: () => http.get<Group[]>('/groups').then((r) => r.data),
  detail: (id: string) => http.get<GroupDetail>(`/groups/${id}`).then((r) => r.data),
  create: (body: { id: string; name: string; description?: string }) =>
    http.post('/groups', body).then((r) => r.data),
  update: (id: string, body: { id: string; name: string; description?: string } & Record<string, unknown>) =>
    http.put(`/groups/${id}`, body).then((r) => r.data),
  remove: (id: string) => http.delete(`/groups/${id}`).then((r) => r.data),
  addSymbol: (id: string, body: { symbol: string; t212_ticker?: string | null }) =>
    http.post(`/groups/${id}/symbols`, body).then((r) => r.data),
  removeSymbol: (id: string, symbol: string) =>
    http.delete(`/groups/${id}/symbols/${symbol}`).then((r) => r.data),
  setSymbolNews: (
    id: string,
    symbol: string,
    body: { news_auto: boolean; news_sources: string[]; news_types: string[] },
  ) => http.put(`/groups/${id}/symbols/${symbol}/news`, body).then((r) => r.data),
  setRecipients: (
    id: string,
    body: { channel: 'telegram' | 'email'; recipients: string[]; event_types: string[] },
  ) => http.put(`/groups/${id}/recipients`, body).then((r) => r.data),
  setNotify: (id: string, recipients: GroupRecipient[]) =>
    http.put(`/groups/${id}/notify`, { recipients }).then((r) => r.data),
  push: (id: string) => http.post(`/groups/${id}/push`).then((r) => r.data),
  syncYaml: () => http.post('/sync-yaml').then((r) => r.data),
  exportYaml: () => http.get<string>('/export-yaml', { responseType: 'text' }).then((r) => r.data),
}

// ── 路由 / 日志 ────────────────────────────────────────────────────────────────
export const notifyApi = {
  routes: (groupId?: string) =>
    http.get<NotifyRoute[]>('/routes', { params: groupId ? { group_id: groupId } : {} }).then((r) => r.data),
  log: (hours: number) =>
    http.get<NotifyLog[]>('/notify-log', { params: { hours } }).then((r) => r.data),
}

// ── T212 行情 / 交易 ──────────────────────────────────────────────────────────
export const t212Api = {
  cash: () => http.get<T212Cash>('/t212/cash').then((r) => r.data),
  positions: () => http.get<Record<string, T212Position>>('/t212/positions').then((r) => r.data),
  search: (q: string, limit = 30) =>
    http.get<T212Instrument[]>('/t212/search', { params: { q, limit } }).then((r) => r.data),
  watchlist: () => http.get<WatchlistItem[]>('/t212/watchlist').then((r) => r.data),
  addWatchlist: (body: { ticker: string; name?: string }) =>
    http.post('/t212/watchlist', body).then((r) => r.data),
  removeWatchlist: (ticker: string) =>
    http.delete(`/t212/watchlist/${encodeURIComponent(ticker)}`).then((r) => r.data),
  activity: (ticker: string, days = 7) =>
    http
      .get<InstrumentActivity>(`/t212/instruments/${encodeURIComponent(ticker)}/activity`, {
        params: { days },
      })
      .then((r) => r.data),
  openOrders: () =>
    http.get<{ items: OpenOrder[]; count: number }>('/t212/orders/open').then((r) => r.data),
  cancelOrder: (id: number) => http.delete(`/t212/orders/${id}`).then((r) => r.data),
  /** 实时现价（每次后端实时拉取，用于按金额→股数换算） */
  quote: (ticker: string) =>
    http.get<{ ticker: string; price: number }>(`/t212/quote/${encodeURIComponent(ticker)}`).then((r) => r.data),
  marketOrder: (body: {
    ticker: string
    side: OrderSide
    quantity?: number | null
    value?: number | null
    currency?: 'USD' | 'EUR'
  }) => http.post('/t212/orders', body).then((r) => r.data),
  limitOrder: (body: {
    ticker: string
    side: OrderSide
    quantity: number
    limitPrice: number
    timeValidity: TimeValidity
  }) => http.post('/t212/orders/limit', body).then((r) => r.data),
  stopOrder: (body: {
    ticker: string
    side: OrderSide
    quantity: number
    stopPrice: number
    timeValidity: TimeValidity
  }) => http.post('/t212/orders/stop', body).then((r) => r.data),
  band: (body: {
    ticker: string
    buyLimitPrice?: number
    sellLimitPrice?: number
    buyQty?: number
    sellQty?: number
    timeValidity: TimeValidity
  }) => http.post('/t212/band', body).then((r) => r.data),
}

// ── 新闻 LLM ──────────────────────────────────────────────────────────────────
export const newsApi = {
  list: (params: { limit?: number; symbol?: string; source?: string; scored?: '1' | '0' } = {}) =>
    http.get<NewsItem[]>('/news', { params }).then((r) => r.data),
  stats: (hours = 72) =>
    http
      .get<NewsStats>('/news/stats', { params: { hours } })
      .then((r) => r.data)
      .catch(() => null as NewsStats | null),
  llmStatus: () =>
    http
      .get<LlmStatus>('/news/llm-status')
      .then((r) => r.data)
      .catch(() => null as LlmStatus | null),
  briefs: (params: { limit?: number; symbol?: string } = {}) =>
    http
      .get<NewsBrief[]>('/news/briefs', { params })
      .then((r) => r.data)
      .catch(() => [] as NewsBrief[]),
}

// ── 交易历史 ──────────────────────────────────────────────────────────────────
export const tradesApi = {
  list: (params: { limit?: number; source?: string; symbol?: string } = {}) =>
    http.get<TradeLog[]>('/trades', { params }).then((r) => r.data),
  clear: () => http.delete<{ deleted: number }>('/trades').then((r) => r.data),
}

// ── 看板 / 标的详情 ───────────────────────────────────────────────────────────
export const dashboardApi = {
  equityCurve: (days = 30) =>
    http
      .get<EquityPoint[]>('/dashboard/equity-curve', { params: { days } })
      .then((r) => r.data)
      .catch(() => [] as EquityPoint[]),
  tradeStats: (days = 30) =>
    http
      .get<TradeStats>('/dashboard/trade-stats', { params: { days } })
      .then((r) => r.data)
      .catch(() => null as TradeStats | null),
  recentFills: (limit = 8) =>
    http
      .get<RecentFill[]>('/dashboard/recent-fills', { params: { limit } })
      .then((r) => r.data)
      .catch(() => [] as RecentFill[]),
  updates: (since?: string, limit = 50) =>
    http
      .get<DataUpdate[]>('/dashboard/updates', { params: { since, limit } })
      .then((r) => r.data)
      .catch(() => [] as DataUpdate[]),
  indicators: (symbol: string) =>
    http
      .get<IndicatorDetail>(`/dashboard/indicators/${encodeURIComponent(symbol)}`)
      .then((r) => r.data)
      .catch(() => null as IndicatorDetail | null),
  sentiment: (symbol: string, days = 7) =>
    http
      .get<SentimentDetail>(`/dashboard/sentiment/${encodeURIComponent(symbol)}`, { params: { days } })
      .then((r) => r.data)
      .catch(() => null as SentimentDetail | null),
}

// ── 量化策略 ──────────────────────────────────────────────────────────────────
export const quantApi = {
  list: () =>
    http
      .get<QuantStrategyStatus[]>('/quant/strategies')
      .then((r) => r.data)
      .catch(() => [] as QuantStrategyStatus[]),
  status: (symbol: string) =>
    http
      .get<QuantStrategyStatus>(`/quant/strategies/${encodeURIComponent(symbol)}`)
      .then((r) => r.data)
      .catch(() => null as QuantStrategyStatus | null),
  start: (
    symbol: string,
    body: {
      t212_ticker: string
      buy_mode: 'ind' | 'market' | 'turning'
      profit_pct: number
      stop_loss: number
      budget_ratio: number
      budget_eur: number
      sell_ratio: number
      interval: number
      max_trades_day: number
      rsi_buy: number
      currency?: 'USD' | 'EUR'
      turn_tf?: 'intraday' | 'daily'
      turn_window?: number
      turn_sample_sec?: number
      turn_beta?: number
      turn_rebound_pct?: number
      turn_recent?: number
      turn_recent_days?: number
      buy_discount_pct?: number
      sell_at_peak?: boolean
      explain_llm?: boolean
    },
  ) =>
    http
      .post<QuantStrategyStatus>(`/quant/strategies/${encodeURIComponent(symbol)}/start`, body)
      .then((r) => r.data),
  stop: (symbol: string) =>
    http
      .post<{ ok: boolean; stopped: boolean }>(
        `/quant/strategies/${encodeURIComponent(symbol)}/stop`,
      )
      .then((r) => r.data),
  trades: (symbol: string, limit = 20) =>
    http
      .get<QuantTradeRecord[]>('/quant/trades', { params: { symbol, limit } })
      .then((r) => r.data)
      .catch(() => [] as QuantTradeRecord[]),
}

// ── 实时行情流 ────────────────────────────────────────────────────────────────
export const streamApi = {
  quote: (symbol: string) =>
    http
      .get<RTQuote>(`/stream/quote/${encodeURIComponent(symbol)}`)
      .then((r) => r.data)
      .catch(() => null as RTQuote | null),
  tradesUrl: (symbol: string) => `${API_BASE}/stream/trades/${encodeURIComponent(symbol)}`,
}

// ── T212 账户管理 ─────────────────────────────────────────────────────────────
export const accountsApi = {
  list: () =>
    http.get<T212Account[]>('/accounts').then((r) => r.data).catch(() => [] as T212Account[]),
  create: (body: { name: string; api_key: string; api_secret?: string; env: 'demo' | 'live' }) =>
    http.post<T212Account>('/accounts', body).then((r) => r.data),
  update: (id: number, body: { name?: string; api_key?: string; api_secret?: string; env?: 'demo' | 'live' }) =>
    http.put<T212Account>(`/accounts/${id}`, body).then((r) => r.data),
  remove: (id: number) =>
    http.delete<{ ok: boolean }>(`/accounts/${id}`).then((r) => r.data),
  activate: (id: number) =>
    http.post<{ ok: boolean; active_id: number; name: string; env: string }>(
      `/accounts/${id}/activate`
    ).then((r) => r.data),
}

// ── 财报 + 研究分析 ──────────────────────────────────────────────────────────
// yfinance 拉取(财报/期权链) + LLM 分析的端点可能耗时数十秒，单独放宽超时到 120s
const SLOW = { timeout: 120000 }
export const researchApi = {
  reports: (symbol: string) =>
    http.get<EarningsReport[]>(`/research/reports/${encodeURIComponent(symbol)}`).then((r) => r.data).catch(() => [] as EarningsReport[]),
  downloadReport: (symbol: string, period?: string | null) =>
    http.post<{ symbol: string; period: string; filename: string; size: number; ok: boolean }>(
      `/research/reports/${encodeURIComponent(symbol)}/download`, { period: period ?? null }, SLOW
    ).then((r) => r.data),
  reportFileUrl: (symbol: string, filename: string) =>
    `${API_BASE}/research/reports/${encodeURIComponent(symbol)}/${encodeURIComponent(filename)}`,
  analyzeBubble: (symbol: string) =>
    http.post<BubbleAnalysis>(`/research/analyze/${encodeURIComponent(symbol)}/bubble`, null, SLOW).then((r) => r.data),
  analyzeStrategy: (symbol: string) =>
    http.post<InvestmentStrategy>(`/research/analyze/${encodeURIComponent(symbol)}/strategy`, null, SLOW).then((r) => r.data),
  latest: (symbol: string) =>
    http.get<ResearchLatest>(`/research/analyze/${encodeURIComponent(symbol)}/latest`).then((r) => r.data).catch(() => ({ history: [] }) as ResearchLatest),
  ensureData: (symbol: string) =>
    http.post<EnsureDataResult>(`/research/ensure-data/${encodeURIComponent(symbol)}`, null, SLOW).then((r) => r.data),
  brief: (symbol: string) =>
    http.post<DailyBriefData>(`/research/brief/${encodeURIComponent(symbol)}`, null, SLOW).then((r) => r.data),
  briefComment: (symbol: string) =>
    http.post<BriefComment>(`/research/brief/${encodeURIComponent(symbol)}/comment`, null, SLOW)
      .then((r) => r.data).catch(() => ({} as BriefComment)),
  briefLatest: (symbol: string) =>
    http.get<DailyBriefData | Record<string, never>>(`/research/brief/${encodeURIComponent(symbol)}/latest`)
      .then((r) => r.data).catch(() => ({} as Record<string, never>)),
}

export interface PriceCandle { t: string; o: number; h: number; l: number; c: number; v: number }

export const pricesApi = {
  history: (symbol: string, days = 30) =>
    http
      .get<{ symbol: string; candles: PriceCandle[] }>(`/t212/prices/${encodeURIComponent(symbol)}`, { params: { days } })
      .then((r) => r.data)
      .catch(() => ({ symbol, candles: [] as PriceCandle[] })),
  /** 按需分钟/小时级 K 线（yfinance 实时）。interval: 1m/5m/15m/30m/60m/1h/1d */
  intraday: (symbol: string, interval = '5m', days = 30) =>
    http
      .get<{ symbol: string; interval: string; candles: PriceCandle[] }>(
        `/t212/prices/${encodeURIComponent(symbol)}/intraday`,
        { params: { interval, days }, timeout: 30000 },
      )
      .then((r) => r.data)
      .catch(() => ({ symbol, interval, candles: [] as PriceCandle[] })),
}

// ── 前瞻短线建议（SSE 流式）──────────────────────────────────────────────────
export const adviceApi = {
  streamUrl: (symbol: string, start?: string, end?: string) => {
    let u = `${API_BASE}/research/advice/stream?symbol=${encodeURIComponent(symbol)}`
    if (start) u += `&start=${encodeURIComponent(start)}`
    if (end) u += `&end=${encodeURIComponent(end)}`
    return u
  },
}

export interface AttributionHistoryItem {
  id: number; start: string; end: string; created_at: string
  pct_change: number | null; result: Record<string, unknown>
}

export const attributionApi = {
  streamUrl: (symbol: string, start: string, end: string, force = false) =>
    `${API_BASE}/attribution/stream?symbol=${encodeURIComponent(symbol)}`
    + `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    + (force ? '&force=true' : ''),
  history: (symbol: string, limit = 10) =>
    http
      .get<AttributionHistoryItem[]>('/attribution/history', { params: { symbol, limit } })
      .then((r) => r.data)
      .catch(() => [] as AttributionHistoryItem[]),
}

export const backtestApi = {
  run: (cfg: BacktestConfig) =>
    http.post<BacktestResult>('/backtest/run', cfg, SLOW).then((r) => r.data),
  dataStatus: () =>
    http
      .get<BacktestDataStatus>('/backtest/data-status')
      .then((r) => r.data)
      .catch(() => ({ symbols: [], ready: 0, total: 0 }) as BacktestDataStatus),
  updateData: () =>
    http
      .post<{ ok: boolean; rows: number; missing: string[] }>('/backtest/update-data', null, SLOW)
      .then((r) => r.data),
  reportBlob: (cfg: BacktestConfig) =>
    http.post('/backtest/report.html', cfg, { ...SLOW, responseType: 'blob' }).then((r) => r.data as Blob),
  exportCsvBlob: (cfg: BacktestConfig) =>
    http.post('/backtest/export.csv', cfg, { ...SLOW, responseType: 'blob' }).then((r) => r.data as Blob),
  exportDriftBlob: (cfg: BacktestConfig) =>
    http.post('/backtest/export-drift.csv', cfg, { ...SLOW, responseType: 'blob' }).then((r) => r.data as Blob),
}

export type { ActivityNews }
