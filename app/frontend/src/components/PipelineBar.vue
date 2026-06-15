<script setup lang="ts">
import { computed } from 'vue'
import type { JobRun } from '@/api/types'
import { fmtTsShort } from '@/composables/format'

const props = defineProps<{
  runs: JobRun[]
  pendingIntents: number
}>()

interface Stage {
  k: string
  v: string
  cls: '' | 'ok' | 'fail' | 'run'
}

const STAGE_MAP: Array<{ job: string; k: string }> = [
  { job: 'job_t212_sync', k: 'T212同步' },
  { job: 'job_daily_prices', k: '行情采集' },
  { job: 'job_signals', k: '指标信号' },
  { job: 'job_sentiment', k: 'LLM情绪' },
  { job: 'job_daily_report', k: '早报推送' },
]

const stages = computed<Stage[]>(() => {
  const latest: Record<string, JobRun> = {}
  for (const r of props.runs) {
    if (!latest[r.job] || r.started_at > latest[r.job].started_at) latest[r.job] = r
  }
  const out: Stage[] = STAGE_MAP.map(({ job, k }) => {
    const r = latest[job]
    if (!r) return { k, v: '—', cls: '' }
    if (r.status === 'ok') return { k, v: `${fmtTsShort(r.finished_at)} ✓`, cls: 'ok' }
    if (r.status === 'running') return { k, v: '运行中…', cls: 'run' }
    return { k, v: `✗ ${(r.detail ?? '').slice(0, 20)}`, cls: 'fail' }
  })
  out.push({
    k: '执行队列',
    v: props.pendingIntents ? `${props.pendingIntents} 待确认` : '空闲',
    cls: props.pendingIntents ? 'run' : 'ok',
  })
  return out
})
</script>

<template>
  <div class="pipe">
    <div v-for="s in stages" :key="s.k" :class="['stage', s.cls]">
      <div class="k">{{ s.k }}</div>
      <div class="v">{{ s.v }}</div>
    </div>
  </div>
</template>
