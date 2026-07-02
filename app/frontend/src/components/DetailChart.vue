<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import {
  AxisPointerComponent,
  BrushComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import { pricesApi } from '@/api/endpoints'
import { palette } from '@/theme'

use([CanvasRenderer, CandlestickChart, BarChart, LineChart, GridComponent, TooltipComponent,
  AxisPointerComponent, BrushComponent, DataZoomComponent, MarkLineComponent, LegendComponent])

const props = defineProps<{ symbol: string; currentPrice?: number | null }>()
const emit = defineEmits<{ analyze: [range: { start: string; end: string }] }>()

interface Candle { t: string; o: number; h: number; l: number; c: number; v: number }

// 周期定义：1D 走日线端点，其余走按需分钟/小时端点(yfinance)
const TFS = [
  { key: '1m', label: '1分', interval: '1m', days: 5, intraday: true },
  { key: '5m', label: '5分', interval: '5m', days: 10, intraday: true },
  { key: '15m', label: '15分', interval: '15m', days: 30, intraday: true },
  { key: '1h', label: '1时', interval: '60m', days: 60, intraday: true },
  { key: '1D', label: '日线', interval: '1d', days: 120, intraday: false },
] as const
type TfKey = typeof TFS[number]['key']
const tf = ref<TfKey>('1D')
const curTf = computed(() => TFS.find((t) => t.key === tf.value)!)

// 客户端缓存（symbol+tf → {ts, candles}，10 分钟 TTL）
const cache = new Map<string, { ts: number; candles: Candle[] }>()
const TTL = 10 * 60 * 1000

const candles = ref<Candle[]>([])
const loading = ref(false)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const chartRef = shallowRef<any>(null)

const sel = ref<{ start: string; end: string; x: number; label: string } | null>(null)

async function load() {
  const sym = props.symbol.toUpperCase()
  const ck = `${sym}|${tf.value}`
  const hit = cache.get(ck)
  if (hit && Date.now() - hit.ts < TTL) { candles.value = hit.candles; return }
  loading.value = true
  sel.value = null
  try {
    const t = curTf.value
    const r = t.intraday
      ? await pricesApi.intraday(sym, t.interval, t.days)
      : await pricesApi.history(sym, t.days)
    candles.value = r.candles || []
    cache.set(ck, { ts: Date.now(), candles: candles.value })
  } catch { candles.value = [] }
  finally { loading.value = false }
}

watch(() => props.symbol, () => { sel.value = null; load() })
watch(tf, load)
onMounted(load)

// 轴标签：分钟级显示 月-日 时:分，日线显示 年-月-日
function fmtAxis(iso: string): string {
  const d = new Date(iso)
  if (curTf.value.intraday) {
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${mm}-${dd} ${hh}:${mi}`
  }
  return iso.slice(0, 10)
}
const axisLabels = computed(() => candles.value.map((c) => fmtAxis(c.t)))

// 默认只显示末尾一段，避免分钟级过密（可拖拽/缩放查看更早）
const zoomStart = computed(() => {
  const n = candles.value.length
  if (n <= 60) return 0
  return Math.max(0, 100 - (60 / n) * 100)
})

const option = computed<EChartsOption>(() => {
  const cs = candles.value
  if (!cs.length) return {} as EChartsOption
  const ohlc = cs.map((c) => [c.o, c.c, c.l, c.h])
  const vols = cs.map((c) => ({ value: c.v, itemStyle: { color: c.c >= c.o ? 'rgba(61,214,140,0.5)' : 'rgba(244,96,78,0.5)' } }))
  const markLine = props.currentPrice
    ? {
        symbol: 'none', silent: true,
        lineStyle: { color: palette.amber, type: 'dashed', width: 1 },
        label: { formatter: `实时 ${props.currentPrice.toFixed(2)}`, color: palette.amber, fontSize: 10, position: 'insideEndTop' },
        data: [{ yAxis: props.currentPrice }],
      }
    : undefined
  return {
    backgroundColor: 'transparent',
    textStyle: { color: palette.muted, fontFamily: 'IBM Plex Mono' },
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: palette.panel, borderColor: palette.line, textStyle: { color: palette.text, fontSize: 11 } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 54, right: 12, top: 12, height: 150 },
      { left: 54, right: 12, top: 176, height: 40 },
    ],
    xAxis: [
      { type: 'category', data: axisLabels.value, boundaryGap: true, axisLabel: { color: palette.faint, fontSize: 9 }, axisLine: { lineStyle: { color: palette.line } } },
      { type: 'category', gridIndex: 1, data: axisLabels.value, axisLabel: { show: false }, axisLine: { lineStyle: { color: palette.line } } },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: palette.faint, fontSize: 9 }, splitLine: { lineStyle: { color: palette.line, type: 'dashed' } } },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    // 拖拽平移 + 滚轮/双指缩放时间维度（T212 式）；两栏联动
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: zoomStart.value, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 14, start: zoomStart.value, end: 100, borderColor: palette.line, fillerColor: 'rgba(232,163,61,0.10)', handleStyle: { color: palette.amber }, textStyle: { color: palette.faint, fontSize: 9 } },
    ],
    // 修复"变白"：未选中区保持满不透明（colorAlpha:1）；选中区仅叠加半透明高亮
    brush: {
      xAxisIndex: 'all', brushLink: 'all', brushType: 'lineX', brushMode: 'single',
      throttleType: 'debounce', throttleDelay: 200, transformable: false,
      brushStyle: { color: 'rgba(232,163,61,0.14)', borderColor: palette.amber },
      inBrush: { colorAlpha: 1 }, outOfBrush: { colorAlpha: 1 },
    },
    series: [
      { name: 'K线', type: 'candlestick', data: ohlc, itemStyle: { color: palette.up, color0: palette.down, borderColor: palette.up, borderColor0: palette.down }, markLine },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols },
    ],
  } as EChartsOption
})

function enableBrush() {
  chartRef.value?.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'lineX', brushMode: 'single' } })
}
watch(option, () => setTimeout(enableBrush, 60))

function popX(a: number, b: number): number {
  try {
    const inst = chartRef.value as unknown as { convertToPixel?: (f: unknown, v: number) => number }
    const px = inst.convertToPixel?.({ xAxisIndex: 0 }, (a + b) / 2)
    if (typeof px === 'number') return px
  } catch { /* fallback */ }
  return 0
}

// 拖拽框选 → 用真实时间戳（分钟精度）作为区间
function onBrushEnd(params: unknown) {
  const p = params as { areas?: Array<{ coordRange?: [number, number] }> }
  const area = p.areas?.[0]
  const n = candles.value.length
  if (!area?.coordRange || !n) { sel.value = null; return }
  let [a, b] = area.coordRange
  a = Math.max(0, Math.round(a)); b = Math.min(n - 1, Math.round(b))
  if (b <= a) { sel.value = null; return }
  setSel(a, b)
}

// 点击单根 K 线 → 取该柱邻域作为分析区间（无需拖拽）
function onClick(params: unknown) {
  const p = params as { seriesType?: string; dataIndex?: number }
  if (p.seriesType !== 'candlestick' && p.seriesType !== 'bar') return
  const n = candles.value.length
  const i = p.dataIndex ?? -1
  if (i < 0 || !n) return
  const a = Math.max(0, i - 3)
  const b = Math.min(n - 1, i + 3)
  setSel(a, b)
}

function setSel(a: number, b: number) {
  const cs = candles.value
  sel.value = {
    start: cs[a].t, end: cs[b].t, x: popX(a, b),
    label: `${fmtAxis(cs[a].t)} → ${fmtAxis(cs[b].t)}`,
  }
}

function doAnalyze() {
  if (!sel.value) return
  emit('analyze', { start: sel.value.start, end: sel.value.end })
}
function clearSel() {
  sel.value = null
  chartRef.value?.dispatchAction({ type: 'brush', areas: [] })
}
</script>

<template>
  <div class="detail-chart">
    <div class="dc-head">
      <span class="col-title" style="margin:0">近走势 · 拖拽/滚轮缩放，框选或点击K线→分析</span>
      <span class="grow" />
      <div class="tf-group">
        <button v-for="t in TFS" :key="t.key" class="tf-btn" :class="{ on: tf === t.key }" @click="tf = t.key">{{ t.label }}</button>
      </div>
      <span v-if="loading" class="faint small">加载中…</span>
    </div>
    <div v-if="!loading && !candles.length" class="faint small empty">
      该周期暂无数据（yfinance 分钟数据在非交易时段/超回溯上限时可能为空，试试更大周期）
    </div>
    <div v-else class="chart-wrap">
      <v-chart ref="chartRef" class="chart" :option="option" autoresize @brushend="onBrushEnd" @click="onClick" />
      <div v-if="sel" class="sel-pop" :style="{ left: Math.max(70, Math.min(sel.x, 320)) + 'px' }">
        <div class="sel-range">{{ sel.label }}</div>
        <button class="sel-btn" @click="doAnalyze">分析该时段</button>
        <button class="sel-x" @click="clearSel">✕</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-chart { border-top: 1px solid var(--line); padding-top: 10px; margin-top: 4px; }
.dc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.grow { flex: 1; }
.col-title { font-size: 11px; color: var(--faint); letter-spacing: .06em; }
.small { font-size: 11px; }
.empty { padding: 24px 0; text-align: center; }
.tf-group { display: flex; gap: 2px; }
.tf-btn {
  background: transparent; border: 1px solid var(--line2); color: var(--muted);
  border-radius: 5px; font-size: 11px; padding: 2px 7px; cursor: pointer;
}
.tf-btn:hover { border-color: var(--amber); color: var(--amber); }
.tf-btn.on { background: rgba(232,163,61,0.14); border-color: var(--amber); color: var(--amber); }
.chart-wrap { position: relative; }
.chart { width: 100%; height: 250px; }
.sel-pop {
  position: absolute; top: 4px; transform: translateX(-50%);
  display: flex; align-items: center; gap: 6px;
  background: var(--panel); border: 1px solid var(--amber);
  border-radius: 8px; padding: 5px 8px; box-shadow: 0 4px 14px rgba(0,0,0,.35);
  z-index: 5; white-space: nowrap;
}
.sel-range { font-family: var(--mono); font-size: 11px; color: var(--muted); }
.sel-btn {
  background: var(--amber); color: #1a1204; border: none; border-radius: 5px;
  padding: 4px 10px; font-size: 12px; font-weight: 600; cursor: pointer;
}
.sel-btn:hover { filter: brightness(1.08); }
.sel-x { background: transparent; border: none; color: var(--faint); cursor: pointer; font-size: 12px; }
</style>
