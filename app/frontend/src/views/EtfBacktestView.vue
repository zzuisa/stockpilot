<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NInputNumber, NSpin, NSwitch, NTag } from 'naive-ui'
import type { EChartsOption } from 'echarts'
import { backtestApi } from '@/api/endpoints'
import type { BacktestConfig, BacktestDataStatus, BacktestResult } from '@/api/types'
import { apiError } from '@/api/client'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import { palette } from '@/theme'

const notify = useNotify()

// ── 配置 ──
const SYMS = ['SPY', 'TLT', 'IEF', 'GLD', 'DBC', 'QQQ', 'BTC']
const LABEL: Record<string, string> = {
  SPY: '标普500', TLT: '长期美债', IEF: '中期美债', GLD: '黄金',
  DBC: '大宗商品', QQQ: '纳指100', BTC: '比特币',
}
const weights = ref<Record<string, number>>({ SPY: 30, TLT: 25, IEF: 15, GLD: 13, DBC: 10, QQQ: 5, BTC: 2 })
const monthlyDca = ref(1400)
const initial = ref(0)
const rebalanceMonths = ref(3)
const startDate = ref('2016-05-01')
const oppOn = ref(true)
const oppTiers = ref<number[]>([5000, 10000, 15000]) // -10% / -20% / -30%

const weightSum = computed(() => SYMS.reduce((a, s) => a + (Number(weights.value[s]) || 0), 0))

const result = ref<BacktestResult | null>(null)
const running = ref(false)
const dataStatus = ref<BacktestDataStatus | null>(null)
const updating = ref(false)

function buildConfig(): BacktestConfig {
  const w: Record<string, number> = {}
  for (const s of SYMS) if (Number(weights.value[s]) > 0) w[s] = Number(weights.value[s])
  const cfg: BacktestConfig = {
    weights: w,
    monthly_dca: monthlyDca.value,
    initial: initial.value,
    rebalance_months: rebalanceMonths.value,
    start: startDate.value || null,
  }
  if (oppOn.value) {
    const tiers = [10, 20, 30]
      .map((dd, i) => ({ dd, amount: Number(oppTiers.value[i]) || 0 }))
      .filter((t) => t.amount > 0)
    cfg.opportunity = [
      { symbol: 'SPY', tiers },
      { symbol: 'QQQ', tiers },
    ]
  } else {
    cfg.opportunity = []
  }
  return cfg
}

async function run() {
  if (weightSum.value <= 0) { notify.err('请至少给一个资产设置权重'); return }
  running.value = true
  try {
    const r = await backtestApi.run(buildConfig())
    if (r.error) { notify.err(r.error); result.value = null }
    else { result.value = r; notify.ok('回测完成') }
  } catch (e) {
    notify.err(`回测失败: ${apiError(e)}`)
  } finally {
    running.value = false
  }
}

async function loadStatus() {
  dataStatus.value = await backtestApi.dataStatus()
}
async function updateData() {
  updating.value = true
  try {
    const r = await backtestApi.updateData()
    notify.ok(`已更新 ${r.rows} 行${r.missing?.length ? '，缺失 ' + r.missing.join(',') : ''}`)
    await loadStatus()
  } catch (e) {
    notify.err(`更新数据失败: ${apiError(e)}`)
  } finally {
    updating.value = false
  }
}

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}
async function exportReport() {
  try {
    const b = await backtestApi.reportBlob(buildConfig())
    window.open(URL.createObjectURL(b), '_blank')
  } catch (e) { notify.err(apiError(e)) }
}
async function exportCsv() {
  try { downloadBlob(await backtestApi.exportCsvBlob(buildConfig()), 'allweather_equity.csv') }
  catch (e) { notify.err(apiError(e)) }
}
async function exportDrift() {
  try { downloadBlob(await backtestApi.exportDriftBlob(buildConfig()), 'allweather_drift.csv') }
  catch (e) { notify.err(apiError(e)) }
}

onMounted(loadStatus)

// ── 展示辅助 ──
const money = (v?: number | null) => (v == null ? '—' : '$' + Math.round(v).toLocaleString())
const signPct = (v?: number | null) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v}%`)
const ddColor = (v: number) => (v <= -20 ? palette.down : v < 0 ? palette.amber : palette.muted)

type Kpi = { label: string; value: string | number; color?: string; small?: boolean }
const kpiCards = computed<Kpi[]>(() => {
  const k = result.value?.kpi
  const rng = result.value?.effective_range
  if (!k) return []
  return [
    { label: '年化复合收益', value: `${k.cagr}%`, color: k.cagr >= 0 ? palette.up : palette.down },
    { label: '年化波动', value: `${k.vol}%` },
    { label: '最大回撤', value: `${k.maxdd}%`, color: palette.down },
    { label: 'Sharpe', value: k.sharpe },
    { label: '期末总资产', value: money(k.final) },
    { label: '累计月定投', value: money(k.dca_total) },
    { label: '机会仓流入', value: money(k.opp_total) },
    { label: '有效区间', value: rng ? `${rng[0]} → ${rng[1]}` : '—', small: true },
  ]
})

const chartOption = computed<EChartsOption>(() => {
  const s = result.value?.series
  if (!s) return {}
  return {
    backgroundColor: 'transparent',
    textStyle: { color: palette.muted, fontFamily: 'IBM Plex Mono' },
    grid: { left: 64, right: 58, top: 24, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.panel,
      borderColor: palette.line,
      textStyle: { color: palette.text, fontSize: 12 },
      formatter: (p: unknown) => {
        const arr = p as Array<{ dataIndex: number }>
        const i = arr[0]?.dataIndex ?? 0
        const ann = s.annualized[i]
        return `${s.dates[i]}<br/>市值 <b>$${Math.round(s.equity[i]).toLocaleString()}</b>`
          + `<br/>回撤 <b>${s.drawdown[i]}%</b>`
          + `<br/>累计收益 <b>${s.cumulative[i]}%</b>`
          + `<br/>当时年化 <b>${ann == null ? '—' : ann + '%'}</b>`
      },
    },
    xAxis: {
      type: 'category', data: s.dates, boundaryGap: false,
      axisLabel: { color: palette.faint, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.line } },
    },
    yAxis: [
      {
        type: 'value', name: '市值', scale: true,
        axisLabel: { color: palette.faint, fontSize: 10, formatter: (v: number) => '$' + Math.round(v / 1000) + 'k' },
        splitLine: { lineStyle: { color: palette.line, type: 'dashed' } },
      },
      {
        type: 'value', name: '回撤%', max: 0,
        axisLabel: { color: palette.faint, fontSize: 10, formatter: '{value}%' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '市值', type: 'line', data: s.equity, yAxisIndex: 0, smooth: true, showSymbol: false,
        lineStyle: { color: palette.amber, width: 1.6 }, areaStyle: { color: 'rgba(232,163,61,0.10)' },
      },
      {
        name: '回撤', type: 'line', data: s.drawdown, yAxisIndex: 1, showSymbol: false,
        lineStyle: { color: palette.down, width: 1 }, areaStyle: { color: 'rgba(244,96,78,0.12)' },
      },
    ],
  }
})
</script>

<template>
  <div class="etf-lab">
    <!-- ═══ 左：参数设置 ═══ -->
    <div class="left">
      <panel-card title="ETF 回测工作台">
        <div class="lab-head">
          <div class="lab-title">ALL WEATHER LAB</div>
          <div class="muted small">全天候投资 + 月度定投 + 机会仓。左侧改参数，右侧直接看结果。</div>
        </div>

        <div class="method-chips">
          <n-tag size="small" :bordered="false" type="warning">股票腿机会仓独立持有</n-tag>
          <n-tag size="small" :bordered="false">月度定投固定</n-tag>
          <n-tag size="small" :bordered="false">总回报口径优先</n-tag>
        </div>

        <div class="sec-label">资产与目标权重</div>
        <div class="weights">
          <div v-for="s in SYMS" :key="s" class="wrow">
            <span class="wsym mono">{{ s }}</span>
            <span class="wlbl muted small">{{ LABEL[s] }}</span>
            <n-input-number v-model:value="weights[s]" :min="0" :max="100" :step="1" size="small" style="width: 92px">
              <template #suffix>%</template>
            </n-input-number>
          </div>
          <div class="wsum" :class="{ warn: weightSum !== 100 }">
            合计 {{ weightSum }}%<span v-if="weightSum !== 100" class="muted small">（将按比例归一化）</span>
          </div>
        </div>

        <div class="sec-label">资金与节奏</div>
        <div class="field"><label>初始资金 $</label>
          <n-input-number v-model:value="initial" :min="0" :step="1000" size="small" />
        </div>
        <div class="field"><label>每月定投 $</label>
          <n-input-number v-model:value="monthlyDca" :min="0" :step="100" size="small" />
        </div>
        <div class="field"><label>主再平衡周期（月，0=不平衡）</label>
          <n-input-number v-model:value="rebalanceMonths" :min="0" :max="36" :step="1" size="small" />
        </div>
        <div class="field"><label>起始日期</label>
          <input v-model="startDate" type="date" class="date-input" />
        </div>

        <div class="sec-label">机会仓（回撤分档加仓 · SPY/QQQ）
          <n-switch v-model:value="oppOn" size="small" style="margin-left: 8px" />
        </div>
        <div v-if="oppOn" class="opp-tiers">
          <div v-for="(t, i) in [10, 20, 30]" :key="t" class="field">
            <label>回撤 -{{ t }}% 加仓 $</label>
            <n-input-number v-model:value="oppTiers[i]" :min="0" :step="1000" size="small" />
          </div>
          <div class="muted small">同一轮回撤每档只触发一次，修复前高后解锁；买入独立持有、不再平衡。</div>
        </div>

        <div class="sec-label">数据与区间</div>
        <div class="data-status" v-if="dataStatus">
          <div class="muted small">本地数据：{{ dataStatus.ready }}/{{ dataStatus.total }} 标的就绪</div>
          <div v-for="d in dataStatus.symbols" :key="d.symbol" class="drow mono small">
            <span class="dsym">{{ d.symbol }}</span>
            <span :class="d.rows > 100 ? 'up' : 'down'">{{ d.rows }} 行</span>
            <span class="muted">{{ d.start || '无' }}{{ d.start ? ' → ' + d.end : '' }}</span>
          </div>
        </div>
        <div class="data-btns">
          <n-button size="small" @click="loadStatus">检查数据状态</n-button>
          <n-button size="small" :loading="updating" @click="updateData">更新数据（yfinance）</n-button>
        </div>

        <n-button type="primary" block size="large" :loading="running" style="margin-top: 14px" @click="run">
          运行回测
        </n-button>
      </panel-card>
    </div>

    <!-- ═══ 右：结果 ═══ -->
    <div class="right">
      <panel-card>
        <template #header>
          <div class="rhead">
            <div>
              <div class="section-label">BACKTEST WORKSPACE</div>
              <div class="rtitle">结果区：同屏查看净值、回撤、对比与日志</div>
            </div>
            <div class="export-btns" v-if="result">
              <n-button size="small" tertiary @click="exportReport">HTML 报告</n-button>
              <n-button size="small" tertiary @click="exportCsv">完整 CSV</n-button>
              <n-button size="small" tertiary @click="exportDrift">漂移 CSV</n-button>
            </div>
          </div>
        </template>

        <n-spin :show="running">
          <div v-if="!result" class="empty muted">
            设置左侧参数后点击「运行回测」。首次使用请先「更新数据」拉取 SPY/IEF/TLT/GLD/DBC/QQQ/BTC 历史行情。
          </div>

          <template v-else>
            <!-- KPI 卡片 -->
            <div class="kpis">
              <div v-for="c in kpiCards" :key="c.label" class="kpi">
                <div class="kpi-k">{{ c.label }}</div>
                <div class="kpi-v" :class="{ sm: c.small }" :style="{ color: c.color || palette.text }">{{ c.value }}</div>
              </div>
            </div>
          </template>
        </n-spin>
      </panel-card>

      <template v-if="result">
        <!-- 多策略核心指标对比 -->
        <panel-card title="多策略核心指标对比">
          <div class="muted small" style="margin-bottom: 8px">
            同一套现金流下，比较 2/3/6/12 个月再平衡、不再平衡、SPY 基准与各资产单独定投
          </div>
          <div class="tbl-wrap">
            <table class="rtable">
              <thead>
                <tr>
                  <th>方案</th><th>类型</th><th>年化复合收益</th><th>年化波动</th><th>最大回撤</th>
                  <th>Sharpe</th><th>期末资产</th><th>总投入</th><th>净盈利</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in result.comparison" :key="i" :class="{ port: c.type === '组合策略' }">
                  <td class="nm">{{ c.name }}</td>
                  <td class="muted">{{ c.type }}</td>
                  <td :style="{ color: c.cagr >= 0 ? palette.up : palette.down }">{{ c.cagr }}%</td>
                  <td>{{ c.vol }}%</td>
                  <td :style="{ color: ddColor(c.maxdd) }">{{ c.maxdd }}%</td>
                  <td>{{ c.sharpe }}</td>
                  <td>{{ money(c.final) }}</td>
                  <td class="muted">{{ money(c.total_invested) }}</td>
                  <td :style="{ color: c.net_profit >= 0 ? palette.up : palette.down }">{{ money(c.net_profit) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </panel-card>

        <!-- 交互式账户状态 -->
        <panel-card title="交互式账户状态">
          <div class="muted small" style="margin-bottom: 6px">
            移动指针查看任意日期的市值、回撤、累计收益与当时年化（主策略：每 {{ result.primary_rebalance }} 个月再平衡）
          </div>
          <base-chart :option="chartOption" height="300px" />
        </panel-card>

        <!-- 每资产利润贡献 / 最大漂移 -->
        <panel-card :title="`每资产利润贡献 / 最大漂移（整体最大漂移 ${result.max_drift_overall}%）`">
          <div class="tbl-wrap">
            <table class="rtable">
              <thead>
                <tr><th>资产</th><th>目标权重</th><th>净投入</th><th>期末市值</th><th>净利润</th><th>最大漂移</th></tr>
              </thead>
              <tbody>
                <tr v-for="a in result.per_asset" :key="a.symbol">
                  <td class="nm">{{ a.symbol }} · {{ a.label }}</td>
                  <td>{{ a.target_weight }}%</td>
                  <td class="muted">{{ money(a.net_invested) }}</td>
                  <td>{{ money(a.final_value) }}</td>
                  <td :style="{ color: a.profit >= 0 ? palette.up : palette.down }">{{ money(a.profit) }}</td>
                  <td :style="{ color: Math.abs(a.max_drift) >= 8 ? palette.amber : palette.muted }">
                    {{ signPct(a.max_drift) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="muted small" style="margin-top: 8px">
            防守资产（债券/黄金/商品）净利润可能不高甚至为负，作用是降低组合波动与回撤；BTC 等因再平衡止盈可能出现"负投入"。
          </div>
        </panel-card>

        <!-- 年度收益 + 机会仓触发 + 再平衡 -->
        <div class="logs-grid">
          <panel-card title="年度收益（时间加权）">
            <div class="tbl-wrap">
              <table class="rtable sm">
                <thead><tr><th>年份</th><th>收益</th></tr></thead>
                <tbody>
                  <tr v-for="y in result.annual_returns" :key="y.year">
                    <td class="nm">{{ y.year }}</td>
                    <td :style="{ color: y.return_pct >= 0 ? palette.up : palette.down }">{{ signPct(y.return_pct) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </panel-card>

          <panel-card :title="`机会仓触发记录（${result.opportunities.length} 次 / 流入 ${money(result.kpi.opp_total)}）`">
            <div class="log-scroll">
              <div v-if="!result.opportunities.length" class="muted small">本区间未触发机会仓加仓。</div>
              <div v-for="(o, i) in result.opportunities" :key="i" class="log-row mono small">
                <span class="muted">{{ o.date }}</span>
                <span class="amber">{{ o.symbol }} 档{{ o.tier }}</span>
                <span class="down">{{ o.drawdown_pct }}%</span>
                <span>{{ money(o.amount) }}</span>
              </div>
            </div>
          </panel-card>

          <panel-card :title="`再平衡记录（${result.rebalances.length} 次）`">
            <div class="log-scroll">
              <div v-if="!result.rebalances.length" class="muted small">不再平衡 / 区间内无再平衡。</div>
              <div v-for="(rb, i) in result.rebalances" :key="i" class="log-row mono small">
                <span class="muted">{{ rb.date }}</span>
                <span class="rb-trades">
                  <span v-for="t in rb.trades" :key="t.symbol" :style="{ color: t.delta_usd >= 0 ? palette.up : palette.down }">
                    {{ t.symbol }}{{ t.delta_usd >= 0 ? '+' : '' }}{{ Math.round(t.delta_usd) }}
                  </span>
                </span>
              </div>
            </div>
          </panel-card>
        </div>

        <div class="disclaimer muted small">
          注：历史数据可能会骗人——过去表现不代表未来。本工具用于验证策略逻辑能否长期执行，非投资建议。
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.etf-lab { display: grid; grid-template-columns: 340px 1fr; gap: 14px; align-items: start; }
@media (max-width: 1100px) { .etf-lab { grid-template-columns: 1fr; } }

.lab-head { margin-bottom: 10px; }
.lab-title { font-family: var(--mono); font-size: 13px; letter-spacing: 0.12em; color: var(--amber); }
.small { font-size: 11px; }
.method-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }

.sec-label { font-size: 11px; color: var(--faint); letter-spacing: 0.06em; margin: 14px 0 8px; }
.weights { display: flex; flex-direction: column; gap: 5px; }
.wrow { display: flex; align-items: center; gap: 8px; }
.wsym { width: 38px; color: var(--text); font-size: 12px; }
.wlbl { flex: 1; }
.wsum { margin-top: 4px; font-size: 12px; color: var(--muted); }
.wsum.warn { color: var(--amber); }

.field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
.field label { font-size: 12px; color: var(--faint); }
.date-input {
  background: var(--panel2); border: 1px solid var(--line); border-radius: 4px;
  color: var(--text); padding: 5px 8px; font-family: var(--mono); font-size: 13px;
}
.opp-tiers { display: flex; flex-direction: column; gap: 8px; }

.data-status { margin-bottom: 8px; }
.drow { display: flex; gap: 8px; padding: 2px 0; }
.dsym { width: 42px; }
.data-btns { display: flex; gap: 8px; flex-wrap: wrap; }

.rhead { display: flex; justify-content: space-between; align-items: flex-start; width: 100%; gap: 12px; }
.rtitle { font-size: 14px; font-weight: 600; margin-top: 2px; }
.export-btns { display: flex; gap: 6px; }

.empty { padding: 40px 10px; text-align: center; font-size: 13px; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
@media (max-width: 720px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
.kpi { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: 11px 13px; }
.kpi-k { font-size: 11px; color: var(--faint); letter-spacing: 0.04em; }
.kpi-v { font-family: var(--mono); font-size: 22px; margin-top: 4px; }
.kpi-v.sm { font-size: 14px; }

.tbl-wrap { overflow-x: auto; }
.rtable { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12.5px; }
.rtable th { text-align: right; color: var(--faint); font-weight: 500; font-size: 11px; padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.rtable td { text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--panel2); white-space: nowrap; }
.rtable th:first-child, .rtable td:first-child { text-align: left; }
.rtable td.nm { color: var(--text); }
.rtable tr.port td.nm { color: var(--amber); }
.rtable.sm td, .rtable.sm th { padding: 5px 8px; }

.logs-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
@media (max-width: 1100px) { .logs-grid { grid-template-columns: 1fr; } }
.log-scroll { max-height: 220px; overflow-y: auto; }
.log-row { display: flex; gap: 8px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--panel2); }
.rb-trades { display: flex; flex-wrap: wrap; gap: 6px; }
.disclaimer { margin-top: 4px; padding: 8px 2px; }
.amber { color: var(--amber); }
.up { color: var(--up); }
.down { color: var(--down); }
</style>
