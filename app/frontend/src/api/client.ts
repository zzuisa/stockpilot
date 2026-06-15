import axios, { type AxiosInstance } from 'axios'

// 运行期推断 API 前缀：
//   - nginx 子路径 (/stockpilot/manage/...)  → /stockpilot/api/v1
//   - 直连 NodePort (/manage/...)            → /api/v1
function detectPrefix(): string {
  return location.pathname.includes('/stockpilot/') ? '/stockpilot' : ''
}

export const PREFIX = detectPrefix()
export const API_BASE = `${PREFIX}/api/v1`

/** /api/v1 之下的业务接口 */
export const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

/** 根路径接口（/health 等，不带 /api/v1） */
export const rootHttp: AxiosInstance = axios.create({
  baseURL: PREFIX || '/',
  timeout: 15000,
})

/** 把后端 HTTPException 的 detail 抽成可读消息 */
export function apiError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined
    if (d?.detail) return d.detail
    if (e.response) return `${e.response.status} ${e.response.statusText}`
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}
