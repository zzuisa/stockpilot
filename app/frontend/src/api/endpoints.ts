import { API_BASE, http, rootHttp } from './client'
import type {
  ActivityNews,
  DashboardSummary,
  Group,
  GroupDetail,
  HealthResponse,
  IndicatorDetail,
  InstrumentActivity,
  JobScheduleResponse,
  JobsResponse,
  NotifyLog,
  NotifyRoute,
  OpenOrder,
  OrderSide,
  QuantStrategyStatus,
  QuantTradeRecord,
  RTQuote,
  SentimentDetail,
  T212Instrument,
  T212Position,
  TimeValidity,
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
  setRecipients: (
    id: string,
    body: { channel: 'telegram' | 'email'; recipients: string[]; event_types: string[] },
  ) => http.put(`/groups/${id}/recipients`, body).then((r) => r.data),
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
  marketOrder: (body: { ticker: string; side: OrderSide; quantity?: number | null; value?: number | null }) =>
    http.post('/t212/orders', body).then((r) => r.data),
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

// ── 看板 / 标的详情 ───────────────────────────────────────────────────────────
export const dashboardApi = {
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
      buy_mode: 'ind' | 'market'
      profit_pct: number
      stop_loss: number
      budget_ratio: number
      budget_eur: number
      sell_ratio: number
      interval: number
      max_trades_day: number
      rsi_buy: number
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

export type { ActivityNews }
