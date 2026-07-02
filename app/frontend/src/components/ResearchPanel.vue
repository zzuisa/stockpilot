<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NButton, NCollapse, NCollapseItem, NDivider, NInput,
  NProgress, NSelect, NSpin, NTag,
} from 'naive-ui'
import type { EChartsOption } from 'echarts'
import { researchApi, t212Api } from '@/api/endpoints'
import type {
  BubbleAnalysis, BubbleLevel, EarningsReport, InvestmentStrategy, ResearchLatest,
} from '@/api/types'
import { apiError } from '@/api/client'
import { fmtTs } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'
import BaseChart from '@/components/charts/BaseChart.vue'
import ProcessFlow, { type FlowStep } from '@/components/ProcessFlow.vue'

defineOptions({ name: 'ResearchPanel' })

const notify = useNotify()

// ── 标的选择 ─────────────────────────────────────────────────────────────────
const symInput = ref('')
const watchlistOptions = ref<{ label: string; value: string }[]>([])
const symbol = computed(() => symInput.value.trim().toUpperCase())

async function loadWatchlist() {
  try {
    const items = await t212Api.watchlist()
    watchlistOptions.value = items.map((w) => ({
      label: `${w.name || w.ticker} (${w.ticker})`,
      value: w.ticker.split('_')[0],
    }))
  } catch {
    // 无需报错，允许手动输入
  }
}

// ── 数据 ─────────────────────────────────────────────────────────────────────
const reports = ref<EarningsReport[]>([])
const latest = ref<ResearchLatest>({ history: [] })
const downloading = ref(false)
const analyzingBubble = ref(false)
const analyzingStrategy = ref(false)

async function loadData() {
  if (!symbol.value) return
  const [rpts, lat] = await Promise.all([
    researchApi.reports(symbol.value),
    researchApi.latest(symbol.value),
  ])
  reports.value = rpts
  latest.value = lat
}

watch(symbol, (v) => { flowSteps.value = []; if (v) loadData() })

onMounted(() => {
  loadWatchlist()
})

async function downloadReport() {
  if (!symbol.value) return
  downloading.value = true
  try {
    const res = await researchApi.downloadReport(symbol.value)
    notify.ok(`${res.symbol} ${res.period} 财报已下载 (${(res.size / 1024).toFixed(1)} KB)`)
    await loadData()
  } catch (e) {
    notify.err(`财报下载失败: ${apiError(e)}`)
  } finally {
    downloading.value = false
  }
}

// ── 流程可视化编排（缺数据自动补全 → 分析） ──────────────────────────────────
const flowSteps = ref<FlowStep[]>([])

async function runWithFlow(analysisName: string, analysisFn: () => Promise<unknown>) {
  flowSteps.value = [
    { name: '采集日线价格', status: 'running' },
    { name: '计算技术指标', status: 'pending' },
    { name: analysisName, status: 'pending' },
  ]
  // ① + ② 按需补全日线 + 指标
  try {
    const res = await researchApi.ensureData(symbol.value)
    res.steps.forEach((s, i) => {
      if (i < 2) flowSteps.value[i] = { name: s.name, status: s.status, detail: s.detail }
    })
    if (res.steps.length < 2) {
      flowSteps.value[1] = { name: '计算技术指标', status: 'failed', detail: '前置步骤失败' }
    }
    if (!res.ready) {
      flowSteps.value[2] = { name: analysisName, status: 'failed', detail: '缺少技术指标数据' }
      notify.err(`${symbol.value} 数据补全失败，无法分析`)
      return
    }
    notify.info(`${symbol.value} 数据已就绪，开始${analysisName}`)
  } catch (e) {
    flowSteps.value[0] = { name: '采集日线价格', status: 'failed', detail: apiError(e) }
    notify.err(`数据补全失败: ${apiError(e)}`)
    return
  }
  // ③ LLM 分析
  flowSteps.value[2] = { name: analysisName, status: 'running' }
  try {
    await analysisFn()
    flowSteps.value[2] = { name: analysisName, status: 'done' }
    notify.ok(`${analysisName}完成`)
    await loadData()
  } catch (e) {
    flowSteps.value[2] = { name: analysisName, status: 'failed', detail: apiError(e) }
    notify.err(`${analysisName}失败: ${apiError(e)}`)
  }
}

async function runBubble() {
  if (!symbol.value) return
  if (!reports.value.length) {
    notify.err('请先下载财报')
    return
  }
  analyzingBubble.value = true
  await runWithFlow('LLM 泡沫分析', () => researchApi.analyzeBubble(symbol.value))
  analyzingBubble.value = false
}

async function runStrategy() {
  if (!symbol.value) return
  analyzingStrategy.value = true
  await runWithFlow('LLM 投资策略分析', () => researchApi.analyzeStrategy(symbol.value))
  analyzingStrategy.value = false
}

// ── 泡沫展示 ─────────────────────────────────────────────────────────────────
const BUBBLE_LABELS: Record<BubbleLevel, string> = {
  normal: '正常', slight: '轻微高估', moderate: '中度高估',
  severe: '严重高估', extreme: '极度高估',
}
const BUBBLE_COLORS: Record<BubbleLevel, 'success' | 'info' | 'warning' | 'error' | 'default'> = {
  normal: 'success', slight: 'info', moderate: 'warning',
  severe: 'error', extreme: 'error',
}
const bubbleData = computed<BubbleAnalysis | null>(() => latest.value.bubble?.data ?? null)

function bubbleProgress(_level: BubbleLevel, pct: number): number {
  const clamp = Math.min(pct, 200)
  return Math.round((clamp / 200) * 100)
}

// ── 策略展示 ─────────────────────────────────────────────────────────────────
const strategyData = computed<InvestmentStrategy | null>(() => latest.value.strategy?.data ?? null)

const REC_LABELS: Record<string, { label: string; type: 'success' | 'warning' | 'error' | 'default' | 'info' }> = {
  buy: { label: '买入', type: 'success' },
  hold: { label: '持有', type: 'info' },
  reduce: { label: '减持', type: 'warning' },
  sell: { label: '卖出', type: 'error' },
}

// ── ECharts: RSI / Price ──────────────────────────────────────────────────────
const rsiOption = computed<EChartsOption>(() => {
  const hist = latest.value.history
  if (!hist.length) return {} as EChartsOption
  const dates = hist.map((r) => r.ts.slice(0, 10))
  const rsiVals = hist.map((r) => r.rsi ?? null)
  const closeVals = hist.map((r) => r.close ?? null)
  return {
    backgroundColor: 'transparent',
    grid: [{ top: 10, bottom: '52%', left: 50, right: 10 },
           { top: '52%', bottom: 30, left: 50, right: 10 }],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: '价格', nameTextStyle: { fontSize: 10 },
        axisLabel: { fontSize: 10 } },
      { type: 'value', gridIndex: 1, name: 'RSI', min: 0, max: 100,
        nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
    ],
    series: [
      { name: '收盘价', type: 'line', data: closeVals, xAxisIndex: 0, yAxisIndex: 0,
        lineStyle: { color: '#E8A33D', width: 2 }, symbol: 'none',
        areaStyle: { color: 'rgba(232,163,61,0.08)' } },
      { name: 'RSI', type: 'line', data: rsiVals, xAxisIndex: 1, yAxisIndex: 1,
        lineStyle: { color: '#5B8FF9', width: 2 }, symbol: 'none' },
      { name: '超买线', type: 'line', data: dates.map(() => 70),
        xAxisIndex: 1, yAxisIndex: 1,
        lineStyle: { color: '#f56c6c', type: 'dashed', width: 1 }, symbol: 'none' },
      { name: '超卖线', type: 'line', data: dates.map(() => 30),
        xAxisIndex: 1, yAxisIndex: 1,
        lineStyle: { color: '#67c23a', type: 'dashed', width: 1 }, symbol: 'none' },
    ],
  }
})

const macdOption = computed<EChartsOption>(() => {
  const hist = latest.value.history
  if (!hist.length) return {} as EChartsOption
  const dates = hist.map((r) => r.ts.slice(0, 10))
  const macdVals = hist.map((r) => r.macd ?? null)
  const signalVals = hist.map((r) => r.macd_signal ?? null)
  const histVals = hist.map((r) => r.macd_hist ?? null)
  return {
    backgroundColor: 'transparent',
    grid: { top: 10, bottom: 30, left: 50, right: 10 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: [
      { name: 'MACD', type: 'line', data: macdVals,
        lineStyle: { color: '#5B8FF9', width: 2 }, symbol: 'none' },
      { name: '信号线', type: 'line', data: signalVals,
        lineStyle: { color: '#f56c6c', width: 2 }, symbol: 'none' },
      {
        name: '柱状图', type: 'bar', data: histVals,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        itemStyle: { color: (p: any) => (p.value ?? 0) >= 0 ? '#67c23a' : '#f56c6c' },
      },
    ],
  } as EChartsOption
})
</script>

<template>
  <panel-card title="个股深度研究 · 财报 + 泡沫 + 策略">
    <!-- 标的选择行 -->
    <div class="sym-row">
      <n-select
        v-model:value="symInput"
        :options="watchlistOptions"
        placeholder="从自选列表选择标的"
        filterable
        clearable
        style="width: 220px"
        size="small"
      />
      <span class="faint">或</span>
      <n-input
        v-model:value="symInput"
        placeholder="手动输入 NVDA"
        size="small"
        style="width: 120px"
        @keyup.enter="loadData"
      />
      <n-button size="small" secondary @click="loadData" :disabled="!symbol">查询</n-button>
    </div>

    <template v-if="symbol">
      <!-- ── 财报区 ───────────────────────────────────────── -->
      <n-divider style="margin: 12px 0 8px">
        <span class="section-title">财报下载</span>
      </n-divider>

      <div class="action-row">
        <n-button
          size="small" type="primary" secondary
          :loading="downloading"
          @click="downloadReport"
        >
          下载最新季报
        </n-button>
        <span class="faint" style="font-size:12px">yfinance 数据，自动保存到服务端</span>
      </div>

      <div v-if="reports.length" class="report-list">
        <div v-for="r in reports" :key="r.id" class="report-item">
          <span class="report-period">{{ r.period }}</span>
          <span class="faint mono" style="font-size:11px">{{ (r.size / 1024).toFixed(1) }}KB</span>
          <span class="faint" style="font-size:11px">{{ fmtTs(r.downloaded_at) }}</span>
          <a
            :href="researchApi.reportFileUrl(r.symbol, r.filename)"
            target="_blank"
            rel="noopener"
            class="report-dl"
          >查看/下载</a>
        </div>
      </div>
      <div v-else class="faint" style="font-size:12px; padding: 4px 0">暂无已下载财报</div>

      <!-- ── 泡沫分析区 ──────────────────────────────────── -->
      <n-divider style="margin: 14px 0 8px">
        <span class="section-title">LLM 泡沫分析</span>
      </n-divider>

      <div class="action-row">
        <n-button
          size="small" type="warning" secondary
          :loading="analyzingBubble"
          @click="runBubble"
        >
          <n-spin v-if="analyzingBubble" size="small" />
          {{ analyzingBubble ? '分析中…' : '执行泡沫分析' }}
        </n-button>
        <span v-if="latest.bubble" class="faint" style="font-size:11px">
          上次: {{ fmtTs(latest.bubble.ts) }}
          · {{ latest.bubble.tokens?.toLocaleString() }} tokens
          · 财报: {{ latest.bubble.report_period ?? '—' }}
        </span>
      </div>

      <template v-if="bubbleData">
        <div class="bubble-result">
          <!-- 级别 + 幅度 -->
          <div class="bubble-header">
            <n-tag :type="BUBBLE_COLORS[bubbleData.bubble_level]" size="medium" :bordered="false">
              {{ BUBBLE_LABELS[bubbleData.bubble_level] }}
            </n-tag>
            <span class="bubble-pct" :class="bubbleData.bubble_level !== 'normal' ? 'down' : 'up'">
              高估 {{ bubbleData.bubble_pct?.toFixed(1) }}%
            </span>
            <span class="faint">合理价参考: <strong>${{ bubbleData.fundamental_value }}</strong></span>
          </div>
          <n-progress
            type="line"
            :percentage="bubbleProgress(bubbleData.bubble_level, bubbleData.bubble_pct)"
            :color="bubbleData.bubble_level === 'normal' ? '#67c23a'
                   : bubbleData.bubble_level === 'slight' ? '#5B8FF9'
                   : bubbleData.bubble_level === 'moderate' ? '#E8A33D'
                   : '#f56c6c'"
            :show-indicator="false"
            style="margin: 8px 0"
          />
          <p class="bubble-summary">{{ bubbleData.summary }}</p>

          <!-- 5个维度 -->
          <div class="factors-grid">
            <div v-for="f in bubbleData.key_factors" :key="f.factor" class="factor-item">
              <div class="factor-head">
                <span class="factor-name">{{ f.factor }}</span>
                <n-tag size="tiny" :bordered="false"
                  :type="f.signal.includes('偏高') || f.signal.includes('超买') ? 'error'
                        : f.signal.includes('偏低') || f.signal.includes('超卖') ? 'success'
                        : 'default'">
                  {{ f.signal }}
                </n-tag>
              </div>
              <p class="factor-detail">{{ f.detail }}</p>
            </div>
          </div>
          <div v-if="bubbleData.risk_warning" class="risk-warning">
            ⚠ {{ bubbleData.risk_warning }}
          </div>
        </div>
      </template>

      <!-- ── 投资策略区 ─────────────────────────────────── -->
      <n-divider style="margin: 14px 0 8px">
        <span class="section-title">一键投资策略（LLM 深度分析）</span>
      </n-divider>

      <div class="action-row">
        <n-button
          size="small" type="primary"
          :loading="analyzingStrategy"
          @click="runStrategy"
        >
          {{ analyzingStrategy ? 'LLM 分析中…' : '生成投资策略' }}
        </n-button>
        <span v-if="latest.strategy" class="faint" style="font-size:11px">
          上次: {{ fmtTs(latest.strategy.ts) }}
          · {{ latest.strategy.tokens?.toLocaleString() }} tokens
        </span>
      </div>

      <!-- 流程可视化：缺数据自动补全 → 分析 -->
      <div v-if="flowSteps.length" class="flow-box">
        <process-flow :steps="flowSteps" title="执行流程" />
      </div>

      <template v-if="strategyData">
        <div class="strategy-result">
          <!-- 推荐 + 置信度 + 持有周期 -->
          <div class="strat-header">
            <n-tag
              :type="REC_LABELS[strategyData.recommendation]?.type ?? 'default'"
              size="large" :bordered="false"
            >
              {{ REC_LABELS[strategyData.recommendation]?.label ?? strategyData.recommendation }}
            </n-tag>
            <div class="strat-meta">
              <span>置信度: <strong>{{ (strategyData.confidence * 100).toFixed(0) }}%</strong></span>
              <span>{{ strategyData.holding_period }}</span>
              <span>趋势: {{ strategyData.trend_phase }} · {{ strategyData.trend_strength }}</span>
            </div>
          </div>

          <!-- 价格区间 -->
          <div class="price-levels">
            <div class="pl-item">
              <span class="pl-k">目标区间</span>
              <span class="pl-v up">${{ strategyData.target_price_low }} – ${{ strategyData.target_price_high }}</span>
            </div>
            <div class="pl-item">
              <span class="pl-k">止损参考</span>
              <span class="pl-v down">${{ strategyData.stop_loss }}</span>
            </div>
            <div class="pl-item">
              <span class="pl-k">RSI 状态</span>
              <span class="pl-v">{{ strategyData.rsi_status }}</span>
            </div>
            <div class="pl-item">
              <span class="pl-k">MACD 状态</span>
              <span class="pl-v">{{ strategyData.macd_status }}</span>
            </div>
          </div>

          <p class="strat-summary">{{ strategyData.summary }}</p>

          <!-- 支撑/压力位 -->
          <div v-if="strategyData.key_levels" class="key-levels">
            <span class="kl-label">关键价位:</span>
            <span class="kl-item up">支撑 ${{ strategyData.key_levels.support1 }} / ${{ strategyData.key_levels.support2 }}</span>
            <span class="kl-item down">压力 ${{ strategyData.key_levels.resistance1 }} / ${{ strategyData.key_levels.resistance2 }}</span>
          </div>

          <!-- 催化剂 -->
          <n-collapse v-if="strategyData.catalysts?.length" style="margin-top: 10px">
            <n-collapse-item title="催化剂" name="catalysts">
              <div v-for="(c, i) in strategyData.catalysts" :key="i" class="catalyst-item">
                <n-tag size="tiny" :type="c.type === '利好' ? 'success' : 'error'" :bordered="false">
                  {{ c.type }}
                </n-tag>
                <span style="font-size:12px">{{ c.description }}</span>
              </div>
            </n-collapse-item>
            <n-collapse-item title="风险因素" name="risks">
              <ul class="risk-list">
                <li v-for="(r, i) in strategyData.risk_factors" :key="i">{{ r }}</li>
              </ul>
            </n-collapse-item>
          </n-collapse>
        </div>
      </template>

      <!-- ── 技术指标图表区 ─────────────────────────────── -->
      <template v-if="latest.history?.length">
        <n-divider style="margin: 14px 0 8px">
          <span class="section-title">技术指标趋势（近30日）</span>
        </n-divider>
        <div class="charts-grid">
          <div class="chart-wrap">
            <div class="chart-title">价格 + RSI</div>
            <base-chart :option="rsiOption" height="260px" />
          </div>
          <div class="chart-wrap">
            <div class="chart-title">MACD</div>
            <base-chart :option="macdOption" height="260px" />
          </div>
        </div>
      </template>
    </template>

    <div v-else class="empty-hint faint">请选择或输入标的代码（如 NVDA）</div>
  </panel-card>
</template>

<style scoped>
.sym-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.section-title {
  font-size: 12px;
  color: var(--muted);
}
.flow-box {
  margin: 8px 0 12px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel2);
}
.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.report-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 4px;
}
.report-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.report-period {
  font-family: var(--mono);
  font-weight: 600;
  color: var(--amber);
  min-width: 60px;
}
.report-dl {
  color: var(--amber);
  text-decoration: none;
  font-size: 11px;
}
.report-dl:hover { text-decoration: underline; }

/* 泡沫 */
.bubble-result {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  margin-top: 6px;
}
.bubble-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.bubble-pct {
  font-family: var(--mono);
  font-size: 18px;
  font-weight: 700;
}
.bubble-summary {
  font-size: 13px;
  line-height: 1.6;
  margin: 8px 0;
  color: var(--text);
}
.factors-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
  margin-top: 8px;
}
.factor-item {
  background: var(--bg);
  border-radius: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line);
}
.factor-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.factor-name {
  font-size: 12px;
  font-weight: 600;
}
.factor-detail {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.5;
  margin: 0;
}
.risk-warning {
  margin-top: 10px;
  font-size: 12px;
  color: var(--down);
  background: rgba(245, 108, 108, 0.08);
  padding: 6px 10px;
  border-radius: 4px;
}

/* 策略 */
.strategy-result {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  margin-top: 6px;
}
.strat-header {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.strat-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--muted);
}
.price-levels {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.pl-item { display: flex; flex-direction: column; }
.pl-k { font-size: 11px; color: var(--faint); }
.pl-v { font-family: var(--mono); font-size: 14px; font-weight: 600; }
.strat-summary {
  font-size: 13px;
  line-height: 1.6;
  margin: 8px 0;
}
.key-levels {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  margin: 6px 0;
}
.kl-label { color: var(--faint); }
.kl-item { font-family: var(--mono); font-weight: 600; }
.catalyst-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 6px;
}
.risk-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.8;
}

/* 图表 */
.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 6px;
}
@media (max-width: 720px) {
  .charts-grid { grid-template-columns: 1fr; }
}
.chart-wrap {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
}
.chart-title {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}

.empty-hint { padding: 20px 0; text-align: center; font-size: 13px; }
.mono { font-family: var(--mono); }
</style>
