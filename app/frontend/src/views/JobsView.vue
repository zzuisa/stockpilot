<script setup lang="ts">
import { h, onMounted, onUnmounted, ref } from 'vue'
import { NButton, NDataTable, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { jobsApi } from '@/api/endpoints'
import type { JobRun, ScheduledJob } from '@/api/types'
import { apiError } from '@/api/client'
import { JOB_CATALOG, JOB_LABELS, type JobCatalogEntry } from '@/composables/jobCatalog'
import { fmtDuration, fmtTs, fmtTsShort } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'

defineOptions({ name: 'JobsView' })

const notify = useNotify()
const recentRuns = ref<JobRun[]>([])
const schedule = ref<ScheduledJob[]>([])
const runningJob = ref<string | null>(null)

async function load() {
  try {
    const [list, sch] = await Promise.all([jobsApi.list(), jobsApi.schedule().catch(() => null)])
    recentRuns.value = list.recent_runs
    if (sch) schedule.value = sch.jobs
  } catch (e) {
    notify.err(`加载任务失败: ${apiError(e)}`)
  }
}

function nextRun(id: string): string {
  const j = schedule.value.find((s) => s.id === id || s.id.startsWith(id))
  return j?.next_run ? fmtTsShort(j.next_run) : '—'
}

async function trigger(id: string) {
  runningJob.value = id
  const label = JOB_LABELS[id] ?? id
  try {
    await jobsApi.run(id)
    notify.ok(`${label} 已触发（后台执行）`)
    setTimeout(load, 1500)
  } catch (e) {
    notify.err(`${label} 触发失败: ${apiError(e)}`)
  } finally {
    runningJob.value = null
  }
}

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  load()
  timer = setInterval(load, 5000)   // 5s 刷新，运行中任务进度实时可见
})
onUnmounted(() => timer && clearInterval(timer))

const catalogColumns: DataTableColumns<JobCatalogEntry> = [
  {
    title: '任务',
    key: 'label',
    render: (j) =>
      h('div', [
        h('div', { style: 'font-weight:500;font-size:13px' }, j.label),
        h('div', { class: 'mono', style: 'font-size:10px;color:var(--faint)' }, j.id),
      ]),
  },
  { title: '说明', key: 'desc', className: 'muted', width: 260 },
  { title: '默认调度', key: 'schedule', className: 'faint', width: 200 },
  {
    title: '下次运行',
    key: 'next',
    className: 'mono',
    render: (j) => h('span', { style: 'color:var(--blue)' }, nextRun(j.id)),
  },
  {
    title: '操作',
    key: 'action',
    align: 'right',
    render: (j) =>
      j.manual
        ? h(
            NButton,
            {
              size: 'tiny',
              secondary: true,
              loading: runningJob.value === j.id,
              onClick: () => trigger(j.id),
            },
            { default: () => '执行' },
          )
        : h('span', { class: 'faint', style: 'font-size:12px' }, '自动'),
  },
]

const runColumns: DataTableColumns<JobRun> = [
  {
    title: '任务',
    key: 'job',
    render: (r) =>
      h('div', [
        h('div', { style: 'font-size:13px' }, JOB_LABELS[r.job] ?? r.job),
        h('div', { class: 'mono', style: 'font-size:10px;color:var(--faint)' }, r.job),
      ]),
  },
  { title: '开始时间', key: 'started_at', className: 'mono muted', render: (r) => fmtTs(r.started_at) },
  {
    title: '耗时',
    key: 'dur',
    className: 'mono muted',
    render: (r) => fmtDuration(r.started_at, r.finished_at),
  },
  {
    title: '状态',
    key: 'status',
    render: (r) => {
      const map: Record<string, { t: 'success' | 'error' | 'warning' | 'default'; l: string }> = {
        ok: { t: 'success', l: '成功' },
        running: { t: 'warning', l: '运行中' },
        skipped: { t: 'default', l: '跳过' },
      }
      const m = map[r.status] ?? { t: 'error', l: '失败' }
      return h(NTag, { type: m.t, size: 'small', bordered: false }, { default: () => m.l })
    },
  },
  {
    title: '详情',
    key: 'detail',
    className: 'muted',
    ellipsis: { tooltip: true },
    // 运行中优先显示实时进度，否则显示结果详情
    render: (r) =>
      r.status === 'running' && r.progress
        ? h('span', { style: 'color:var(--amber)' }, `⏳ ${r.progress}`)
        : (r.detail ?? '—'),
  },
]
</script>

<template>
  <div>
    <panel-card title="任务目录 · 点击立即执行">
      <n-data-table :columns="catalogColumns" :data="JOB_CATALOG" :bordered="false" size="small" />
    </panel-card>

    <panel-card title="最近运行记录">
      <template #header>
        <n-button size="tiny" quaternary style="margin-left: auto" @click="load">⟳ 刷新</n-button>
      </template>
      <n-data-table
        :columns="runColumns"
        :data="recentRuns"
        :bordered="false"
        size="small"
        :max-height="480"
        virtual-scroll
      />
    </panel-card>
  </div>
</template>
