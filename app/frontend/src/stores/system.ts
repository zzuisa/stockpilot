import { defineStore } from 'pinia'
import { ref } from 'vue'
import { systemApi } from '@/api/endpoints'
import type { DashboardSummary, HealthResponse } from '@/api/types'

export const useSystemStore = defineStore('system', () => {
  const health = ref<HealthResponse | null>(null)
  const summary = ref<DashboardSummary | null>(null)

  async function loadHealth() {
    try {
      health.value = await systemApi.health()
    } catch {
      /* 网络抖动时静默 */
    }
  }

  async function loadSummary() {
    try {
      summary.value = await systemApi.summary()
    } catch {
      /* 概览数据缺失不阻塞页面 */
    }
  }

  return { health, summary, loadHealth, loadSummary }
})
