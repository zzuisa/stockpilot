<script setup lang="ts">
import { h, onMounted, onUnmounted, ref } from 'vue'
import { NDataTable, NGrid, NGi, NTag, NStatistic } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { jobsApi } from '@/api/endpoints'
import type { JobRun, SummaryPosition } from '@/api/types'
import { useSystemStore } from '@/stores/system'
import PanelCard from '@/components/PanelCard.vue'
import PipelineBar from '@/components/PipelineBar.vue'
import { fmtMoney } from '@/composables/format'

defineOptions({ name: 'OverviewView' })

const system = useSystemStore()
const runs = ref<JobRun[]>([])
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  await Promise.all([system.loadHealth(), system.loadSummary()])
  try {
    const data = await jobsApi.list()
    runs.value = data.recent_runs
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 60000)
})
onUnmounted(() => timer && clearInterval(timer))

const posColumns: DataTableColumns<SummaryPosition> = [
  { title: 'TICKER', key: 'ticker', className: 'mono' },
  { title: '数量', key: 'quantity', align: 'right', className: 'mono muted' },
  {
    title: '均价',
    key: 'avg_price',
    align: 'right',
    className: 'mono muted',
    render: (r) => (r.avg_price != null ? r.avg_price.toFixed(2) : '—'),
  },
  {
    title: '现价',
    key: 'current_price',
    align: 'right',
    className: 'mono',
    render: (r) => (r.current_price != null ? r.current_price.toFixed(2) : '—'),
  },
  {
    title: '盈亏',
    key: 'ppl',
    align: 'right',
    className: 'mono',
    render: (r) =>
      h(
        'span',
        { style: { color: (r.ppl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' } },
        fmtMoney(r.ppl, '€', true),
      ),
  },
]
</script>

<template>
  <div>
    <pipeline-bar
      :runs="runs"
      :pending-intents="system.summary?.pending_intents?.length ?? 0"
      style="margin-bottom: 16px"
    />

    <panel-card title="系统集成">
      <n-grid :cols="4" :x-gap="10" :y-gap="10" responsive="screen" item-responsive>
        <n-gi
          v-for="(v, k) in system.health?.integrations ?? {}"
          :key="k"
          span="4 s:2 m:1"
        >
          <div class="hcard">
            <div class="hk">{{ String(k).toUpperCase() }}</div>
            <n-tag :type="v ? 'success' : 'error'" size="small" :bordered="false">
              {{ v ? '✓ 已连接' : '✗ 未配置' }}
            </n-tag>
          </div>
        </n-gi>
      </n-grid>
    </panel-card>

    <panel-card v-if="system.summary?.account" title="账户快照">
      <n-grid :cols="3" :x-gap="14" responsive="screen" item-responsive>
        <n-gi span="3 s:1">
          <n-statistic label="总资产" :value="fmtMoney(system.summary.account.total, '€')" />
        </n-gi>
        <n-gi span="3 s:1">
          <n-statistic label="可用现金" :value="fmtMoney(system.summary.account.free_cash, '€')" />
        </n-gi>
        <n-gi span="3 s:1">
          <n-statistic label="浮动盈亏" :value="fmtMoney(system.summary.account.ppl, '€', true)" />
        </n-gi>
      </n-grid>
      <div class="meta-row">
        <span>
          待确认意向单
          <n-tag size="small" :type="system.summary.pending_intents.length ? 'warning' : 'default'" :bordered="false">
            {{ system.summary.pending_intents.length }}
          </n-tag>
        </span>
        <span>
          24h 信号
          <n-tag size="small" :bordered="false">{{ system.summary.signals_24h.length }}</n-tag>
        </span>
      </div>
    </panel-card>

    <panel-card v-if="system.summary?.positions?.length" title="持仓">
      <n-data-table
        :columns="posColumns"
        :data="system.summary.positions"
        :bordered="false"
        size="small"
      />
    </panel-card>
  </div>
</template>

<style scoped>
.hcard {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
}
.hk {
  font-size: 11px;
  color: var(--faint);
  letter-spacing: 0.06em;
  margin-bottom: 6px;
}
.meta-row {
  display: flex;
  gap: 24px;
  margin-top: 14px;
  font-size: 13px;
  color: var(--muted);
}
</style>
