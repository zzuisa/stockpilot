<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { NButton, NGrid, NGi, NStatistic, NTag, useDialog } from 'naive-ui'
import type { EChartsOption } from 'echarts'
import { dashboardApi, jobsApi, t212Api, tradesApi } from '@/api/endpoints'
import type { EquityPoint, JobRun, RecentFill, T212Cash, T212Position, TradeStats } from '@/api/types'
import { useSystemStore } from '@/stores/system'
import { palette } from '@/theme'
import PanelCard from '@/components/PanelCard.vue'
import PipelineBar from '@/components/PipelineBar.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { useNotify } from '@/composables/useNotify'
import { apiError } from '@/api/client'
import { fmtMoney, fmtTs, shortTicker } from '@/composables/format'

defineOptions({ name: 'OverviewView' })

const dialog = useDialog()
const notify = useNotify()
const clearing = ref(false)

function clearData() {
  dialog.warning({
    title: '清除当前数据',
    content: '将清空交易历史（近期成交 / 交易统计 / 今日盈亏）。账户现金、持仓、资产曲线不受影响。确认清除？',
    positiveText: '清除',
    negativeText: '取消',
    onPositiveClick: async () => {
      clearing.value = true
      try {
        const { deleted } = await tradesApi.clear()
        notify.ok(`已清除 ${deleted} 条交易记录`)
        await load()
      } catch (e) {
        notify.err(`清除失败: ${apiError(e)}`)
      } finally {
        clearing.value = false
      }
    },
  })
}

const system = useSystemStore()
const runs = ref<JobRun[]>([])
const equity = ref<EquityPoint[]>([])
const stats = ref<TradeStats | null>(null)
const positions = ref<T212Position[]>([])
const recent = ref<RecentFill[]>([])
const cash = ref<T212Cash | null>(null)   // 实时账户现金(10s 刷新)
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  await Promise.all([system.loadHealth(), system.loadSummary()])
  const [j, eq, st, pos, tr, cs] = await Promise.all([
    jobsApi.list().catch(() => null),
    dashboardApi.equityCurve(30),
    dashboardApi.tradeStats(30),
    t212Api.positions().catch(() => ({}) as Record<string, T212Position>),
    dashboardApi.recentFills(8),
    t212Api.cash().catch(() => null as T212Cash | null),
  ])
  if (j) runs.value = j.recent_runs
  equity.value = eq
  stats.value = st
  positions.value = Object.values(pos).filter((p) => p.quantity)
  recent.value = tr
  if (cs) cash.value = cs
}

// 总资产 / 可用现金 / 浮动盈亏：优先实时现金，回退到账户快照
const kpiTotal = computed(() => cash.value?.total ?? system.summary?.account?.total ?? null)
const kpiFree = computed(() => cash.value?.free ?? system.summary?.account?.free_cash ?? null)
const kpiPpl = computed(() => cash.value?.ppl ?? system.summary?.account?.ppl ?? null)

onMounted(() => {
  load()
  timer = setInterval(load, 10000)   // 10s 刷新全部数据
})
onUnmounted(() => timer && clearInterval(timer))

// 最新一笔快照(现金/持仓)
const lastEq = computed(() => equity.value.at(-1) ?? null)

// ── 图表通用样式 ──
const axisLine = { lineStyle: { color: palette.line2 } }
const splitLine = { lineStyle: { color: palette.line, type: 'dashed' as const } }
const baseTextStyle = { color: palette.muted, fontFamily: 'IBM Plex Mono, monospace' }

// 资产曲线
const equityOption = computed<EChartsOption>(() => ({
  backgroundColor: 'transparent',
  textStyle: baseTextStyle,
  grid: { left: 56, right: 16, top: 16, bottom: 28 },
  tooltip: { trigger: 'axis', valueFormatter: (v) => `€${Number(v).toFixed(2)}` },
  xAxis: {
    type: 'category',
    data: equity.value.map((p) => fmtTs(p.ts).slice(5, 16)),
    axisLine, axisLabel: { color: palette.faint, fontSize: 10 },
  },
  yAxis: {
    type: 'value', scale: true, axisLabel: { color: palette.faint, fontSize: 10 },
    splitLine,
  },
  series: [{
    name: '总资产', type: 'line', smooth: true, showSymbol: false,
    data: equity.value.map((p) => p.total),
    lineStyle: { color: palette.amber, width: 2 },
    areaStyle: { color: 'rgba(232,163,61,0.12)' },
  }],
}))

// 现金 vs 持仓
const cashOption = computed<EChartsOption>(() => {
  const cash = lastEq.value?.free_cash ?? system.summary?.account?.free_cash ?? 0
  const invested = lastEq.value?.invested ?? 0
  return donut('现金 / 持仓', [
    { name: '可用现金', value: round2(cash), itemStyle: { color: palette.blue } },
    { name: '持仓市值', value: round2(invested), itemStyle: { color: palette.amber } },
  ])
})

// 持仓占比
const allocOption = computed<EChartsOption>(() =>
  donut('持仓占比', positions.value
    .map((p) => ({ name: shortTicker(p.ticker), value: round2(p.currentValue ?? 0) }))
    .filter((d) => d.value > 0)),
)

// 持仓浮盈排行
const pplOption = computed<EChartsOption>(() => {
  const data = [...positions.value]
    .filter((p) => p.ppl != null)
    .sort((a, b) => (a.ppl ?? 0) - (b.ppl ?? 0))
  return {
    backgroundColor: 'transparent',
    textStyle: baseTextStyle,
    grid: { left: 70, right: 24, top: 8, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', axisLabel: { color: palette.faint, fontSize: 10 }, splitLine },
    yAxis: {
      type: 'category', data: data.map((p) => shortTicker(p.ticker)),
      axisLine, axisLabel: { color: palette.faint, fontSize: 10 },
    },
    series: [{
      type: 'bar',
      data: data.map((p) => ({
        value: round2(p.ppl ?? 0),
        itemStyle: { color: (p.ppl ?? 0) >= 0 ? palette.up : palette.down },
      })),
      barMaxWidth: 16,
    }],
  }
})

// 已实现盈亏走势(按日)
const pnlByDayOption = computed<EChartsOption>(() => {
  const d = stats.value?.by_day ?? []
  return {
    backgroundColor: 'transparent',
    textStyle: baseTextStyle,
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category', data: d.map((x) => x.day.slice(5)),
      axisLine, axisLabel: { color: palette.faint, fontSize: 10 },
    },
    yAxis: { type: 'value', axisLabel: { color: palette.faint, fontSize: 10 }, splitLine },
    series: [{
      type: 'bar',
      data: d.map((x) => ({
        value: x.pnl,
        itemStyle: { color: x.pnl >= 0 ? palette.up : palette.down },
      })),
      barMaxWidth: 20,
    }],
  }
})

function donut(name: string, data: Array<{ name: string; value: number; itemStyle?: object }>): EChartsOption {
  return {
    backgroundColor: 'transparent',
    textStyle: baseTextStyle,
    tooltip: { trigger: 'item', valueFormatter: (v) => `€${Number(v).toFixed(2)}` },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: palette.faint, fontSize: 10 } },
    color: [palette.amber, palette.blue, palette.up, palette.down, '#9b8cf5', '#5ad1c4', '#e8c33d'],
    series: [{
      name, type: 'pie', radius: ['42%', '66%'], center: ['50%', '45%'],
      avoidLabelOverlap: true, label: { show: false }, data,
    }],
  }
}

const round2 = (v: number) => Math.round(v * 100) / 100

const recentReason: Record<string, string> = {
  set_profit_limit: '挂止盈', profit_limit: '止盈成交', hard_stop: '硬止损',
  signal_stop: '指标止损', market_buy: '市价买', ind_buy: '指标买',
}
</script>

<template>
  <div>
    <!-- 工具条 -->
    <div class="ov-toolbar">
      <span class="faint" style="font-size: 12px">每 10 秒自动刷新</span>
      <n-button size="small" type="error" secondary :loading="clearing" @click="clearData">
        清除当前数据
      </n-button>
    </div>

    <!-- KPI 条 -->
    <n-grid :cols="4" :x-gap="12" :y-gap="12" responsive="screen" item-responsive style="margin-bottom: 14px">
      <n-gi span="2 m:1">
        <div class="kpi"><div class="kpi-k">总资产</div><div class="kpi-v">{{ fmtMoney(kpiTotal, '€') }}</div></div>
      </n-gi>
      <n-gi span="2 m:1">
        <div class="kpi"><div class="kpi-k">可用现金</div><div class="kpi-v">{{ fmtMoney(kpiFree, '€') }}</div></div>
      </n-gi>
      <n-gi span="2 m:1">
        <div class="kpi">
          <div class="kpi-k">浮动盈亏</div>
          <div class="kpi-v" :style="{ color: (kpiPpl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }">
            {{ fmtMoney(kpiPpl, '€', true) }}
          </div>
        </div>
      </n-gi>
      <n-gi span="2 m:1">
        <div class="kpi">
          <div class="kpi-k">今日已实现</div>
          <div class="kpi-v" :style="{ color: (stats?.today_pnl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }">
            {{ fmtMoney(stats?.today_pnl, '', true) }}
          </div>
        </div>
      </n-gi>
    </n-grid>

    <!-- 图表网格 -->
    <n-grid :cols="2" :x-gap="14" :y-gap="14" responsive="screen" item-responsive>
      <n-gi span="2">
        <panel-card title="资产曲线 · 近30天">
          <base-chart v-if="equity.length" :option="equityOption" height="260px" />
          <div v-else class="empty">暂无快照数据（每 30 分钟由 T212 同步任务写入）</div>
        </panel-card>
      </n-gi>

      <n-gi span="2 m:1">
        <panel-card title="持仓占比">
          <base-chart v-if="positions.length" :option="allocOption" height="240px" />
          <div v-else class="empty">当前无持仓</div>
        </panel-card>
      </n-gi>
      <n-gi span="2 m:1">
        <panel-card title="现金 / 持仓">
          <base-chart v-if="lastEq || system.summary?.account" :option="cashOption" height="240px" />
          <div v-else class="empty">暂无账户数据</div>
        </panel-card>
      </n-gi>

      <n-gi span="2 m:1">
        <panel-card title="持仓浮盈排行">
          <base-chart v-if="positions.length" :option="pplOption" height="240px" />
          <div v-else class="empty">当前无持仓</div>
        </panel-card>
      </n-gi>
      <n-gi span="2 m:1">
        <panel-card title="已实现盈亏 · 按日">
          <base-chart v-if="stats?.by_day?.length" :option="pnlByDayOption" height="240px" />
          <div v-else class="empty">暂无已实现盈亏记录</div>
        </panel-card>
      </n-gi>
    </n-grid>

    <!-- 交易统计 + 近期成交 -->
    <n-grid :cols="2" :x-gap="14" :y-gap="14" responsive="screen" item-responsive>
      <n-gi span="2 m:1">
        <panel-card title="交易统计 · 近30天">
          <n-grid :cols="3" :x-gap="10">
            <n-gi><n-statistic label="胜率" :value="(stats?.win_rate ?? 0) + '%'" /></n-gi>
            <n-gi><n-statistic label="平仓笔数" :value="stats?.trade_count ?? 0" /></n-gi>
            <n-gi>
              <n-statistic label="已实现盈亏">
                <span :style="{ color: (stats?.realized_pnl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }">
                  {{ fmtMoney(stats?.realized_pnl, '', true) }}
                </span>
              </n-statistic>
            </n-gi>
          </n-grid>
          <div class="winloss">
            <span class="up">胜 {{ stats?.win ?? 0 }}</span> ·
            <span class="down">负 {{ stats?.loss ?? 0 }}</span> ·
            <span class="faint">总成交 {{ stats?.total_fills ?? 0 }}</span>
          </div>
        </panel-card>
      </n-gi>
      <n-gi span="2 m:1">
        <panel-card title="近期成交">
          <div v-if="!recent.length" class="empty">暂无成交</div>
          <div v-for="(t, i) in recent" :key="i" class="trade-row">
            <span class="mono faint">{{ fmtTs(t.ts).slice(5, 16) }}</span>
            <span class="tag-sym">{{ t.symbol || shortTicker(t.ticker) }}</span>
            <n-tag size="tiny" :bordered="false" :type="t.side === 'buy' ? 'success' : 'error'">
              {{ t.side === 'buy' ? '买' : '卖' }}
            </n-tag>
            <span class="mono faint small">{{ t.quantity }}×{{ t.price?.toFixed(4) }}</span>
            <span v-if="t.reason" class="faint small">{{ recentReason[t.reason] ?? t.reason }}</span>
            <span v-if="t.pnl != null" class="mono small" :style="{ color: t.pnl >= 0 ? 'var(--up)' : 'var(--down)', marginLeft: 'auto' }">
              {{ t.pnl >= 0 ? '+' : '' }}{{ t.pnl.toFixed(2) }}
            </span>
            <span v-else class="mono faint small" style="margin-left:auto">{{ t.value_eur?.toFixed(2) }}</span>
          </div>
        </panel-card>
      </n-gi>
    </n-grid>

    <!-- 流水线 + 集成健康 -->
    <pipeline-bar :runs="runs" :pending-intents="system.summary?.pending_intents?.length ?? 0" style="margin: 4px 0 14px" />
    <panel-card title="系统集成">
      <n-grid :cols="4" :x-gap="10" :y-gap="10" responsive="screen" item-responsive>
        <n-gi v-for="(v, k) in system.health?.integrations ?? {}" :key="k" span="4 s:2 m:1">
          <div class="hcard">
            <div class="hk">{{ String(k).toUpperCase() }}</div>
            <n-tag :type="v ? 'success' : 'error'" size="small" :bordered="false">{{ v ? '✓ 已连接' : '✗ 未配置' }}</n-tag>
          </div>
        </n-gi>
      </n-grid>
    </panel-card>
  </div>
</template>

<style scoped>
.ov-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.kpi {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
}
.kpi-k { font-size: 11px; color: var(--faint); letter-spacing: 0.06em; }
.kpi-v { font-family: var(--mono); font-size: 22px; margin-top: 4px; }
.empty { color: var(--faint); text-align: center; padding: 40px 0; font-size: 13px; }
.winloss { margin-top: 10px; font-size: 12px; }
.hcard { background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; }
.hk { font-size: 11px; color: var(--faint); letter-spacing: 0.06em; margin-bottom: 6px; }
.trade-row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 0; border-bottom: 1px solid var(--panel2); font-size: 12px;
}
.trade-row:last-child { border-bottom: none; }
.small { font-size: 11px; }
.tag-sym {
  font-family: var(--mono); font-size: 11px;
  background: rgba(232, 163, 61, 0.12); color: var(--amber);
  padding: 1px 6px; border-radius: 3px;
}
</style>
