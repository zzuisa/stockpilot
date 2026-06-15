<script setup lang="ts">
import { h } from 'vue'
import { NButton, NDataTable, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { OpenOrder } from '@/api/types'
import { shortTicker } from '@/composables/format'
import PanelCard from './PanelCard.vue'

defineProps<{
  orders: OpenOrder[]
  loading: boolean
}>()
const emit = defineEmits<{
  cancel: [id: number]
  refresh: []
}>()

const TYPE_LABEL: Record<string, string> = {
  LIMIT: '限价单',
  STOP: '止损单',
  MARKET: '市价单',
  limit: '限价单',
  stop: '止损单',
  market: '市价单',
}

const columns: DataTableColumns<OpenOrder> = [
  {
    title: 'Ticker',
    key: 'ticker',
    render: (o) => h('span', { class: 'tag-amber' }, shortTicker(o.ticker)),
  },
  {
    title: '类型',
    key: 'type',
    className: 'muted',
    render: (o) => TYPE_LABEL[o.type ?? o.orderType ?? ''] ?? (o.type ?? '限价单'),
  },
  {
    title: '方向',
    key: 'side',
    render: (o) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: (o.quantity ?? 0) >= 0 ? 'success' : 'error' },
        { default: () => ((o.quantity ?? 0) >= 0 ? '买入' : '卖出') },
      ),
  },
  {
    title: '数量',
    key: 'quantity',
    align: 'right',
    className: 'mono',
    render: (o) => Math.abs(o.quantity ?? 0),
  },
  {
    title: '价格',
    key: 'price',
    align: 'right',
    className: 'mono amber',
    render: (o) => o.limitPrice ?? o.stopPrice ?? '—',
  },
  { title: '有效期', key: 'timeValidity', className: 'faint', render: (o) => o.timeValidity ?? '—' },
  {
    title: '状态',
    key: 'status',
    render: (o) => h(NTag, { size: 'small', bordered: false }, { default: () => o.status ?? 'OPEN' }),
  },
  {
    title: '操作',
    key: 'action',
    align: 'right',
    render: (o) =>
      h(
        NButton,
        { size: 'tiny', type: 'error', secondary: true, onClick: () => emit('cancel', o.id) },
        { default: () => '取消' },
      ),
  },
]
</script>

<template>
  <panel-card v-if="orders.length || loading" title="当前挂单">
    <template #header>
      <n-tag size="small" type="warning" :bordered="false">{{ orders.length }}</n-tag>
      <n-button size="tiny" quaternary style="margin-left: auto" :loading="loading" @click="emit('refresh')">
        ⟳ 刷新
      </n-button>
    </template>
    <n-data-table :columns="columns" :data="orders" :bordered="false" size="small" :loading="loading" />
  </panel-card>
</template>

<style scoped>
:deep(.tag-amber) {
  font-family: var(--mono);
  font-size: 12px;
  background: rgba(232, 163, 61, 0.12);
  color: var(--amber);
  padding: 2px 7px;
  border-radius: 3px;
}
</style>
