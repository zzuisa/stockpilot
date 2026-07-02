<script setup lang="ts">
import { computed, ref } from 'vue'
import { NTag } from 'naive-ui'
import type { EChartsOption } from 'echarts'
import type { DailyBriefData } from '@/api/types'
import { fmtTs } from '@/composables/format'
import BaseChart from '@/components/charts/BaseChart.vue'

const props = defineProps<{ data: DailyBriefData }>()

type Tab = 'take' | 'trend' | 'options' | 'levels'
const tab = ref<Tab>('take')
const TABS: { key: Tab; label: string; en: string }[] = [
  { key: 'take', label: '今日结论', en: "TODAY'S TAKE" },
  { key: 'trend', label: '趋势证据', en: 'TREND' },
  { key: 'options', label: '期权结论', en: 'OPTIONS' },
  { key: 'levels', label: '观察条件', en: 'LEVELS' },
]

const up = computed(() => (props.data.change_pct ?? 0) >= 0)

// 芯片配色
function chipType(kind: 'pattern' | 'momentum' | 'signal' | 'flow', v: string):
  'success' | 'error' | 'warning' | 'default' {
  if (kind === 'pattern') return v.includes('多头') ? 'success' : v.includes('空头') ? 'error' : 'warning'
  if (kind === 'momentum') return v.includes('增强') || v === '向上' ? 'success' : v.includes('衰减') ? 'error' : 'warning'
  if (kind === 'signal') return v.includes('积极') ? 'success' : v.includes('防守') ? 'error' : 'warning'
  if (kind === 'flow') return v === '流入' ? 'success' : v === '流出' ? 'error' : 'default'
  return 'default'
}

// 主图：卡尔曼快/慢线 + ±2σ 置信带 + 多空 regime 背景
const kfOption = computed<EChartsOption | null>(() => {
  const t = props.data.trend
  if (!t) return null
  const x = t.dates.map((d) => d.slice(5))
  const bandDiff = t.band_upper.map((u, i) => +(u - t.band_lower[i]).toFixed(2))
  const areas: [Record<string, unknown>, Record<string, unknown>][] = []
  let st = 0
  for (let i = 1; i <= t.regime.length; i++) {
    if (i === t.regime.length || t.regime[i] !== t.regime[st]) {
      areas.push([
        { xAxis: x[st], itemStyle: { color: t.regime[st] === 'bull' ? 'rgba(61,214,140,0.08)' : 'rgba(231,76,60,0.08)' } },
        { xAxis: x[i - 1] },
      ])
      st = i
    }
  }
  return {
    grid: { left: 46, right: 10, top: 26, bottom: 22 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 9 }, data: ['Close', 'Fast', 'Slow'] },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 9 } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 9 } },
    series: [
      { name: 'lo', type: 'line', data: t.band_lower, stack: 'band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 } },
      { name: 'band', type: 'line', data: bandDiff, stack: 'band', symbol: 'none', silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(232,163,61,0.10)' } },
      { name: 'Close', type: 'line', data: t.close, symbol: 'none', lineStyle: { width: 1, color: '#94a3b8' }, z: 3 },
      { name: 'Fast', type: 'line', data: t.fast, symbol: 'none', smooth: true, lineStyle: { width: 2, color: '#e056fd' }, z: 5, markArea: { silent: true, data: areas } },
      { name: 'Slow', type: 'line', data: t.slow, symbol: 'none', smooth: true, lineStyle: { width: 2, color: '#22d3ee' }, z: 4 },
    ],
  }
})

// 趋势价差色阶柱（Layer 2）+ 周线共振（Layer 2.1）
function spreadOption(arr: number[] | undefined, dates: string[]): EChartsOption | null {
  if (!arr?.length) return null
  const mx = Math.max(...arr.map((v) => Math.abs(v)), 0.001)
  return {
    grid: { left: 46, right: 10, top: 6, bottom: 16 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates.map((d) => d.slice(5)), axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
    series: [{
      type: 'bar',
      data: arr.map((v) => {
        const a = Math.min(1, Math.abs(v) / mx)
        return { value: v, itemStyle: { color: v >= 0 ? `rgba(34,160,90,${0.25 + 0.65 * a})` : `rgba(231,76,60,${0.25 + 0.65 * a})` } }
      }),
    }],
  }
}
const spreadDaily = computed(() => props.data.trend ? spreadOption(props.data.trend.spread, props.data.trend.dates) : null)
const spreadWeekly = computed(() => props.data.trend ? spreadOption(props.data.trend.weekly_spread, props.data.trend.dates) : null)
const mfUsd = computed(() => {
  const v = props.data.trend?.money_flow_usd ?? 0
  const a = Math.abs(v)
  const num = a >= 1e8 ? `${(a / 1e8).toFixed(2)}亿` : `${(a / 1e4).toFixed(0)}万`
  return { txt: `${v >= 0 ? '净流入' : '净流出'} ${num}`, up: v >= 0 }
})

// Gamma Exposure by Strike
const gammaOption = computed<EChartsOption | null>(() => {
  const o = props.data.options
  if (!o || !o.gamma_by_strike?.length) return null
  const gs = o.gamma_by_strike
  const wall = (v: number | null, color: string, name: string) =>
    v == null ? null : { xAxis: String(v), lineStyle: { color, type: 'solid' as const },
      label: { formatter: name, fontSize: 9, color } }
  return {
    grid: { left: 52, right: 12, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: gs.map((g) => String(g.strike)), axisLabel: { fontSize: 9 } },
    yAxis: { type: 'value', name: 'GEX', nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 9 } },
    series: [{
      type: 'bar',
      data: gs.map((g) => ({ value: g.gex, itemStyle: { color: g.gex >= 0 ? '#3dd68c' : '#e74c3c' } })),
      markLine: {
        symbol: 'none',
        data: [wall(o.call_wall, '#e74c3c', 'Call Wall'),
               wall(o.put_wall, '#3dd68c', 'Put Wall'),
               wall(o.spot, '#e8a33d', '现价')].filter(Boolean) as object[],
      },
    }],
  }
})

// 价位标尺定位（put_wall 底 → call_wall 顶）
const ruler = computed(() => {
  const o = props.data.options
  const price = props.data.price
  if (!o || o.call_wall == null || o.put_wall == null || o.call_wall <= o.put_wall) return null
  const span = o.call_wall - o.put_wall
  const pct = (v: number) => Math.max(0, Math.min(100, ((v - o.put_wall!) / span) * 100))
  return {
    top: o.call_wall, bottom: o.put_wall, price,
    pricePos: 100 - pct(price),
    upPct: ((o.call_wall - price) / price * 100),
    downPct: ((o.put_wall - price) / price * 100),
  }
})

const c = computed(() => props.data.comment ?? {})
</script>

<template>
  <div class="brief">
    <!-- 头部 -->
    <header class="bf-head">
      <div class="bf-head-top">
        <span class="bf-title">美股盘前日报 <b>DAILY BRIEF</b></span>
        <span class="bf-date mono">{{ fmtTs(data.ts).slice(0, 16) }}</span>
      </div>
      <div class="bf-tabs">
        <button v-for="t in TABS" :key="t.key" :class="['bf-tab', { on: tab === t.key }]" @click="tab = t.key">
          {{ t.label }}
        </button>
      </div>
    </header>

    <!-- ① 今日结论 -->
    <section v-if="tab === 'take'" class="bf-body">
      <div class="sym-row">
        <div>
          <div class="sym">{{ data.symbol }}</div>
          <div class="sym-sub faint">US.{{ data.symbol }}</div>
        </div>
      </div>
      <div class="price-row">
        <span class="price">${{ data.price?.toFixed(2) }}</span>
        <span class="chg" :class="up ? 'up' : 'down'">
          {{ up ? '▲' : '▼' }} {{ data.change_pct }}%
        </span>
        <span class="faint small">昨收 ${{ data.prev_close?.toFixed(2) }}</span>
      </div>
      <div class="chips">
        <div class="chip">
          <span class="chip-k">格局</span>
          <n-tag size="small" :type="chipType('pattern', data.chips.pattern)" :bordered="false">{{ data.chips.pattern }}</n-tag>
        </div>
        <div class="chip">
          <span class="chip-k">动量</span>
          <n-tag size="small" :type="chipType('momentum', data.chips.momentum)" :bordered="false">{{ data.chips.momentum }}</n-tag>
        </div>
        <div class="chip">
          <span class="chip-k">信号</span>
          <n-tag size="small" :type="chipType('signal', data.chips.signal)" :bordered="false">{{ data.chips.signal }}</n-tag>
        </div>
      </div>
      <div class="take-title">今日核心结论 · TODAY'S TAKE</div>
      <p class="take">{{ c.core_take || '—' }}</p>
      <div class="nav-title faint small">60 秒看完三件事</div>
      <button class="nav-item" @click="tab = 'trend'"><span class="n">1</span> 趋势还在吗？ <span class="arrow">→</span></button>
      <button class="nav-item" @click="tab = 'options'"><span class="n">2</span> 期权怎么说？ <span class="arrow">→</span></button>
      <button class="nav-item" @click="tab = 'levels'"><span class="n">3</span> 盯哪个价位？ <span class="arrow">→</span></button>
    </section>

    <!-- ② 趋势证据 -->
    <section v-else-if="tab === 'trend'" class="bf-body">
      <div class="q-head"><span class="q-num">1</span> 趋势还在吗？
        <n-tag v-if="data.trend" size="small" :type="data.trend.trend_label.includes('强') || data.trend.trend_label.includes('多') ? 'success' : 'error'" :bordered="false" style="margin-left:auto">{{ data.trend.trend_label }}</n-tag>
      </div>
      <template v-if="data.trend">
        <div class="layer-title faint small">价格 + 卡尔曼快/慢线 + ±2σ 带 + 多空 regime</div>
        <base-chart v-if="kfOption" :option="kfOption" height="190px" />
        <div class="layer-title faint small">Layer 2 · 趋势价差 (Trend Spread)</div>
        <base-chart v-if="spreadDaily" :option="spreadDaily" height="80px" />
        <div class="layer-title faint small">Layer 2.1 · 周线共振 (Weekly Resonance)</div>
        <base-chart v-if="spreadWeekly" :option="spreadWeekly" height="70px" />
      </template>
      <base-chart v-else :option="{ grid:{left:44,right:12,top:14,bottom:22}, xAxis:{type:'category',data:(data.price_history||[]).map(p=>p.ts.slice(5))}, yAxis:{type:'value',scale:true}, series:[{type:'line',smooth:true,showSymbol:false,data:(data.price_history||[]).map(p=>p.close),areaStyle:{opacity:0.12}}] }" height="180px" />
      <div class="chips">
        <div class="chip"><span class="chip-k">格局</span>
          <n-tag size="small" :type="chipType('pattern', data.chips.pattern)" :bordered="false">{{ data.chips.pattern }}</n-tag></div>
        <div class="chip"><span class="chip-k">主力资金</span>
          <n-tag size="small" :type="mfUsd.up ? 'success' : 'error'" :bordered="false">{{ data.trend ? mfUsd.txt + '美元' : data.chips.money_flow }}</n-tag></div>
        <div class="chip"><span class="chip-k">相对量能</span>
          <n-tag size="small" type="default" :bordered="false">{{ data.chips.vol_label }}</n-tag></div>
      </div>
      <p class="comment">{{ c.trend_comment || '—' }}</p>
    </section>

    <!-- ③ 期权结论 -->
    <section v-else-if="tab === 'options'" class="bf-body">
      <div class="q-head"><span class="q-num">2</span> 期权市场怎么说？</div>
      <template v-if="data.options">
        <base-chart v-if="gammaOption" :option="gammaOption" height="200px" />
        <div class="metrics">
          <div class="metric"><div class="m-k">GEX</div><div class="m-v">{{ (data.options.gex / 1000).toFixed(1) }}K</div></div>
          <div class="metric"><div class="m-k">PCR(OI)</div><div class="m-v">{{ data.options.pcr_oi ?? '—' }}</div></div>
          <div class="metric"><div class="m-k">IV</div><div class="m-v">{{ data.options.iv_atm != null ? (data.options.iv_atm * 100).toFixed(1) + '%' : '—' }}</div></div>
        </div>
        <div class="wall-row">
          <span>Put Wall <b class="up">${{ data.options.put_wall ?? '—' }}</b></span>
          <span>预期波动 <b>±{{ data.options.expected_move_pct ?? '—' }}%</b></span>
          <span>Call Wall <b class="down">${{ data.options.call_wall ?? '—' }}</b></span>
        </div>
        <p class="comment">{{ c.options_comment || '—' }}</p>
      </template>
      <div v-else class="faint" style="padding:16px 0">该标的暂无期权数据（非美股期权标的）。</div>
    </section>

    <!-- ④ 观察条件 -->
    <section v-else class="bf-body">
      <div class="q-head"><span class="q-num">3</span> 今天盯哪个价位？</div>
      <template v-if="ruler">
        <div class="ruler-wrap">
          <div class="ruler">
            <div class="r-band" />
            <div class="r-mark call" :style="{ top: '0%' }">Call Wall ${{ ruler.top }}</div>
            <div class="r-price" :style="{ top: ruler.pricePos + '%' }">现价 ${{ ruler.price?.toFixed(2) }}</div>
            <div class="r-mark put" :style="{ top: '100%' }">Put Wall ${{ ruler.bottom }}</div>
          </div>
        </div>
        <div class="lv-row">
          <div class="lv down"><div class="lv-k">下方支撑</div><div class="lv-v">${{ ruler.bottom }}</div><div class="lv-p">{{ ruler.downPct.toFixed(2) }}%</div></div>
          <div class="lv up"><div class="lv-k">上方压力</div><div class="lv-v">${{ ruler.top }}</div><div class="lv-p">+{{ ruler.upPct.toFixed(2) }}%</div></div>
        </div>
        <p class="comment">{{ c.levels_comment || '—' }}</p>
      </template>
      <div v-else class="faint" style="padding:16px 0">无期权墙数据，暂无价位标尺。</div>
    </section>
  </div>
</template>

<style scoped>
.brief { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--panel); }
.bf-head { padding: 12px 14px; background: linear-gradient(135deg, rgba(232,163,61,0.12), rgba(61,214,140,0.06)); border-bottom: 1px solid var(--line); }
.bf-head-top { display: flex; justify-content: space-between; align-items: baseline; }
.bf-title { font-size: 12px; color: var(--muted); letter-spacing: 0.05em; }
.bf-title b { color: var(--amber); }
.bf-date { font-size: 11px; color: var(--faint); }
.bf-tabs { display: flex; gap: 4px; margin-top: 10px; }
.bf-tab { flex: 1; font-size: 12px; padding: 6px 4px; background: none; border: 1px solid var(--line); border-radius: 5px; color: var(--muted); cursor: pointer; }
.bf-tab.on { color: var(--amber); border-color: var(--amber-dim); background: rgba(232,163,61,0.08); }
.bf-body { padding: 14px; }

.sym { font-size: 22px; font-weight: 700; }
.sym-sub { font-size: 11px; }
.price-row { display: flex; align-items: baseline; gap: 12px; margin: 8px 0 12px; }
.price { font-size: 34px; font-weight: 700; }
.chg { font-size: 15px; font-weight: 600; }
.up { color: var(--up); } .down { color: var(--down); }

.chips { display: flex; gap: 16px; flex-wrap: wrap; margin: 10px 0; }
.chip { display: flex; flex-direction: column; gap: 3px; }
.chip-k { font-size: 10px; color: var(--faint); }

.take-title { font-size: 11px; color: var(--faint); margin: 14px 0 6px; letter-spacing: 0.05em; }
.take { font-size: 17px; font-weight: 600; line-height: 1.5; margin: 0 0 14px; }
.nav-title { margin: 6px 0; }
.nav-item { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; padding: 10px 12px; margin-bottom: 6px; background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; color: var(--text); font-size: 13px; cursor: pointer; }
.nav-item:hover { border-color: var(--amber-dim); }
.nav-item .n { width: 18px; height: 18px; border-radius: 50%; background: var(--amber-dim); color: var(--amber); font-size: 11px; display: flex; align-items: center; justify-content: center; }
.nav-item .arrow { margin-left: auto; color: var(--faint); }

.q-head { font-size: 15px; font-weight: 600; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.q-num { width: 20px; height: 20px; border-radius: 50%; background: var(--amber-dim); color: var(--amber); font-size: 12px; display: flex; align-items: center; justify-content: center; }
.layer-title { margin: 10px 0 2px; }
.comment { font-size: 13px; line-height: 1.6; color: var(--muted); margin: 12px 0 0; padding: 10px 12px; background: var(--panel2); border-radius: 6px; border-left: 2px solid var(--amber-dim); }

.metrics { display: flex; gap: 10px; margin: 12px 0 8px; }
.metric { flex: 1; text-align: center; padding: 8px; background: var(--panel2); border-radius: 6px; }
.m-k { font-size: 10px; color: var(--faint); }
.m-v { font-size: 16px; font-weight: 700; margin-top: 2px; }
.wall-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin: 6px 2px; }

.ruler-wrap { display: flex; justify-content: center; padding: 20px 0; }
.ruler { position: relative; width: 60%; height: 200px; }
.r-band { position: absolute; left: 50%; top: 0; bottom: 0; width: 8px; transform: translateX(-50%); border-radius: 4px; background: linear-gradient(to bottom, rgba(231,76,60,0.5), rgba(232,163,61,0.3), rgba(61,214,140,0.5)); }
.r-mark, .r-price { position: absolute; left: 50%; transform: translate(-50%, -50%); font-size: 11px; white-space: nowrap; padding: 2px 8px; border-radius: 4px; }
.r-mark.call { color: var(--down); background: rgba(231,76,60,0.12); }
.r-mark.put { color: var(--up); background: rgba(61,214,140,0.12); }
.r-price { color: var(--amber); background: var(--panel); border: 1px solid var(--amber-dim); font-weight: 600; }
.lv-row { display: flex; gap: 10px; margin-top: 8px; }
.lv { flex: 1; padding: 10px; border-radius: 6px; background: var(--panel2); text-align: center; }
.lv-k { font-size: 10px; color: var(--faint); }
.lv-v { font-size: 16px; font-weight: 700; margin: 2px 0; }
.lv-p { font-size: 11px; }
.lv.up .lv-v { color: var(--down); } .lv.down .lv-v { color: var(--up); }
.small { font-size: 11px; } .faint { color: var(--faint); } .mono { font-family: var(--mono); }
</style>
