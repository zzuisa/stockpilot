<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NDataTable, NInput, NSelect, NTag, NButton } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { tradesApi } from '@/api/endpoints'
import type { TradeLog } from '@/api/types'
import { apiError } from '@/api/client'
import { fmtTs, shortTicker } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'

defineOptions({ name: 'TradesView' })

const notify = useNotify()
const rows = ref<TradeLog[]>([])
const loading = ref(false)
const source = ref<string>('')
const symbol = ref<string>('')

const sourceOptions = [
  { label: '全部来源', value: '' },
  { label: '手动', value: 'manual' },
  { label: '量化', value: 'quant' },
]

const ORDER_TYPE: Record<string, string> = {
  market: '市价',
  limit: '限价',
  stop: '止损',
  band_sell: '波段卖',
  band_buy: '波段买',
}
const REASON: Record<string, string> = {
  set_profit_limit: '挂止盈',
  profit_limit: '止盈成交',
  hard_stop: '硬止损',
  signal_stop: '指标止损',
  market_buy: '市价买入',
  ind_buy: '指标买入',
}

async function load() {
  loading.value = true
  try {
    rows.value = await tradesApi.list({
      limit: 300,
      source: source.value || undefined,
      symbol: symbol.value.trim() || undefined,
    })
  } catch (e) {
    notify.err(`加载交易历史失败: ${apiError(e)}`)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const columns: DataTableColumns<TradeLog> = [
  { title: '时间', key: 'ts', width: 150, className: 'mono muted', render: (r) => fmtTs(r.ts) },
  {
    title: '来源',
    key: 'source',
    width: 64,
    render: (r) =>
      h(NTag, { size: 'small', bordered: false, type: r.source === 'quant' ? 'info' : 'default' },
        { default: () => (r.source === 'quant' ? '量化' : '手动') }),
  },
  {
    title: '标的',
    key: 'symbol',
    render: (r) => h('span', { class: 'tag-amber' }, r.symbol || shortTicker(r.t212_ticker)),
  },
  {
    title: '方向',
    key: 'side',
    width: 60,
    render: (r) =>
      h(NTag, { size: 'small', bordered: false, type: r.side === 'buy' ? 'success' : 'error' },
        { default: () => (r.side === 'buy' ? '买入' : '卖出') }),
  },
  { title: '类型', key: 'order_type', width: 76, className: 'muted', render: (r) => ORDER_TYPE[r.order_type ?? ''] ?? (r.order_type ?? '—') },
  { title: '数量', key: 'quantity', align: 'right', className: 'mono', render: (r) => (r.quantity != null ? r.quantity : '—') },
  { title: '价格', key: 'price', align: 'right', className: 'mono', render: (r) => (r.price != null ? r.price.toFixed(2) : '—') },
  {
    title: '盈亏',
    key: 'pnl',
    align: 'right',
    className: 'mono',
    render: (r) =>
      r.pnl == null
        ? '—'
        : h('span', { style: { color: r.pnl >= 0 ? 'var(--up)' : 'var(--down)' } },
            `${r.pnl >= 0 ? '+' : ''}${r.pnl.toFixed(2)}`),
  },
  { title: '说明', key: 'reason', className: 'faint', render: (r) => REASON[r.reason ?? ''] ?? (r.reason ?? '—') },
  {
    title: '状态',
    key: 'status',
    width: 80,
    render: (r) => {
      const t = r.status === 'filled' ? 'success' : r.status === 'failed' ? 'error' : 'warning'
      const l = r.status === 'filled' ? '成交' : r.status === 'failed' ? '失败' : '已提交'
      return h(NTag, { size: 'small', bordered: false, type: t }, { default: () => l })
    },
  },
]
</script>

<template>
  <panel-card title="交易历史">
    <template #header>
      <n-tag size="small" :bordered="false">{{ rows.length }} 条</n-tag>
      <span class="grow" />
      <n-select v-model:value="source" :options="sourceOptions" size="small" style="width: 120px" @update:value="load" />
      <n-input v-model:value="symbol" placeholder="标的(如 NVDA)" size="small" style="width: 140px"
        @keyup.enter="load" />
      <n-button size="small" quaternary :loading="loading" @click="load">⟳ 刷新</n-button>
    </template>
    <n-data-table
      :columns="columns"
      :data="rows"
      :bordered="false"
      size="small"
      :loading="loading"
      :max-height="560"
      virtual-scroll
    />
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
