// 统一时区格式化：Europe/Berlin（服务器时区，自动处理夏令时）

const berlinFmt = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Berlin',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})
const berlinFmtSec = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Berlin',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

/** 完整时间戳 → "YYYY-MM-DD HH:MM[:SS]"（柏林时区） */
export function fmtBerlin(d: Date | string | number, withSec = false): string {
  try {
    const date = d instanceof Date ? d : new Date(d)
    return (withSec ? berlinFmtSec : berlinFmt).format(date).replace('T', ' ')
  } catch {
    return '—'
  }
}

export function fmtTs(ts: string | null | undefined): string {
  if (!ts) return '—'
  return fmtBerlin(ts, false)
}

/** 去掉年份，只留 MM-DD HH:MM */
export function fmtTsShort(ts: string | null | undefined): string {
  if (!ts) return '—'
  try {
    return berlinFmt.format(new Date(ts)).slice(5)
  } catch {
    return '—'
  }
}

/** 数字 → 金额字符串 */
export function fmtMoney(v: number | null | undefined, prefix = '', signed = false): string {
  if (v == null) return '—'
  const sign = signed && v > 0 ? '+' : ''
  return `${sign}${prefix}${v.toFixed(2)}`
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return '—'
  return v.toFixed(digits)
}

/** 运行耗时 */
export function fmtDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '—'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

/** Ticker → 简短代码（NVDA_US_EQ → NVDA） */
export function shortTicker(ticker: string | null | undefined): string {
  if (!ticker) return ''
  return ticker.split('_')[0]
}

// ── 情绪标签 ──
const SENT_LABELS: Record<string, string> = {
  '2': '极正',
  '1': '利好',
  '0': '中性',
  '-1': '利空',
  '-2': '极负',
}

export function sentLabel(s: number | null | undefined): string {
  if (s == null) return '—'
  return SENT_LABELS[String(s)] ?? '—'
}

export function sentType(s: number | null | undefined): 'success' | 'error' | 'default' {
  if (s == null || s === 0) return 'default'
  return s > 0 ? 'success' : 'error'
}
