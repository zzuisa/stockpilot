<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NDataTable, NSelect, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { notifyApi } from '@/api/endpoints'
import type { NotifyLog } from '@/api/types'
import { apiError } from '@/api/client'
import { fmtTs } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'

defineOptions({ name: 'LogsView' })

const notify = useNotify()
const logs = ref<NotifyLog[]>([])
const hours = ref(24)

const hourOptions = [
  { label: '近 6h', value: 6 },
  { label: '近 24h', value: 24 },
  { label: '近 3d', value: 72 },
  { label: '近 7d', value: 168 },
]

async function load() {
  try {
    logs.value = await notifyApi.log(hours.value)
  } catch (e) {
    notify.err(`加载日志失败: ${apiError(e)}`)
  }
}

onMounted(load)

const STATUS: Record<string, { t: 'success' | 'error' | 'warning'; l: string }> = {
  sent: { t: 'success', l: 'sent' },
  failed: { t: 'error', l: 'failed' },
  skipped: { t: 'warning', l: 'skipped' },
}

const columns: DataTableColumns<NotifyLog> = [
  { title: '时间', key: 'ts', className: 'mono muted', width: 150, render: (r) => fmtTs(r.ts) },
  { title: '事件', key: 'event_type', width: 130 },
  {
    title: '分组',
    key: 'group_id',
    render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => r.group_id ?? '—' }),
  },
  { title: '标的', key: 'symbol', render: (r) => r.symbol ?? '—' },
  { title: '渠道', key: 'channel' },
  { title: '接收者', key: 'recipient', className: 'mono muted', ellipsis: { tooltip: true }, width: 160 },
  {
    title: '状态',
    key: 'status',
    render: (r) => {
      const s = STATUS[r.status] ?? { t: 'warning' as const, l: r.status }
      return h('div', [
        h(NTag, { type: s.t, size: 'small', bordered: false }, { default: () => s.l }),
        r.error_msg
          ? h('span', { class: 'down', style: 'font-size:11px;margin-left:4px' }, r.error_msg.slice(0, 40))
          : null,
      ])
    },
  },
]
</script>

<template>
  <panel-card title="推送日志">
    <template #header>
      <n-tag size="small" :bordered="false">{{ logs.length }} 条</n-tag>
      <span class="grow" />
      <n-select
        v-model:value="hours"
        :options="hourOptions"
        size="small"
        style="width: 120px"
        @update:value="load"
      />
    </template>
    <n-data-table
      :columns="columns"
      :data="logs"
      :bordered="false"
      size="small"
      :max-height="540"
      virtual-scroll
    />
  </panel-card>
</template>
