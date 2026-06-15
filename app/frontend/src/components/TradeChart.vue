<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import type { RTTrade } from '@/api/types'

const props = defineProps<{
  trades: RTTrade[]
  currentPrice: number | null
  windowMin?: number
}>()

const canvas = ref<HTMLCanvasElement | null>(null)
const windowMin = ref(props.windowMin ?? 2)
let rafId = 0

// ── helpers ────────────────────────────────────────────────────────────────

function tickSize(range: number): number {
  if (range <= 0.05) return 0.002
  if (range <= 0.2) return 0.01
  if (range <= 1) return 0.05
  if (range <= 5) return 0.1
  if (range <= 20) return 0.5
  if (range <= 100) return 1
  return 5
}

function roundTick(p: number, tick: number): number {
  return Math.round(p / tick) * tick
}

function fmtVol(v: number): string {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M'
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'K'
  return v.toString()
}

function fmtTime(ms: number): string {
  const d = new Date(ms)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':')
}

// ── draw loop ──────────────────────────────────────────────────────────────

function draw() {
  const cvs = canvas.value
  if (!cvs) return
  const ctx = cvs.getContext('2d')
  if (!ctx) return

  // Sync canvas pixel size with CSS size
  const W = cvs.offsetWidth
  const H = cvs.offsetHeight
  if (cvs.width !== W || cvs.height !== H) {
    cvs.width = W
    cvs.height = H
  }

  const LABEL_H = 22        // time axis at bottom
  const PROFILE_W = 100     // right panel width
  const PRICE_LABEL_W = 40  // price labels inside profile
  const CHART_W = W - PROFILE_W
  const CHART_H = H - LABEL_H

  const now = Date.now()
  const windowMs = windowMin.value * 60_000
  const t0 = now - windowMs

  const visible = props.trades.filter((t) => t.t >= t0)

  // ── background ────────────────────────────────────────────────────────────
  ctx.fillStyle = '#0C1117'
  ctx.fillRect(0, 0, W, H)

  if (visible.length === 0) {
    ctx.fillStyle = '#54616E'
    ctx.font = '11px monospace'
    ctx.textAlign = 'center'
    ctx.fillText('等待成交数据… (美股盘中 15:30–22:00 ET)', W / 2, H / 2)
    rafId = requestAnimationFrame(draw)
    return
  }

  // ── price range ───────────────────────────────────────────────────────────
  let pLow = Infinity, pHigh = -Infinity
  for (const t of visible) {
    if (t.p < pLow) pLow = t.p
    if (t.p > pHigh) pHigh = t.p
  }
  const rawRange = pHigh - pLow || 0.02
  const pad = rawRange * 0.3
  const pMin = pLow - pad
  const pMax = pHigh + pad
  const pRange = pMax - pMin

  const tick = tickSize(rawRange)

  const pToY = (p: number) => ((pMax - p) / pRange) * CHART_H
  const tToX = (t: number) => ((t - t0) / windowMs) * CHART_W

  // ── grid ──────────────────────────────────────────────────────────────────
  ctx.strokeStyle = '#1A2530'
  ctx.lineWidth = 1

  // horizontal (price)
  const pGridStart = Math.ceil(pMin / tick) * tick
  for (let p = pGridStart; p <= pMax + 1e-9; p = Math.round((p + tick) * 1e8) / 1e8) {
    const y = pToY(p)
    if (y < 0 || y > CHART_H) continue
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(CHART_W, y)
    ctx.stroke()
  }

  // vertical (time, every 30s)
  const GRID_T = 30_000
  const tGrid0 = Math.ceil(t0 / GRID_T) * GRID_T
  ctx.fillStyle = '#54616E'
  ctx.font = '9px monospace'
  for (let t = tGrid0; t <= now; t += GRID_T) {
    const x = tToX(t)
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, CHART_H)
    ctx.stroke()
    ctx.textAlign = 'center'
    ctx.fillText(fmtTime(t), x, H - 5)
  }

  // ── trade bubbles ─────────────────────────────────────────────────────────
  for (const t of visible) {
    const x = tToX(t.t)
    const y = pToY(t.p)
    const r = Math.max(2, Math.min(20, Math.log(t.v + 1) * 2.8))

    if (t.d > 0) ctx.fillStyle = 'rgba(61,214,140,0.72)'
    else if (t.d < 0) ctx.fillStyle = 'rgba(244,96,78,0.72)'
    else ctx.fillStyle = 'rgba(160,160,160,0.45)'

    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fill()
  }

  // ── volume-at-price profile (right panel) ─────────────────────────────────
  ctx.fillStyle = '#0D1520'
  ctx.fillRect(CHART_W, 0, PROFILE_W, H)

  // divider
  ctx.strokeStyle = '#1E2933'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(CHART_W, 0)
  ctx.lineTo(CHART_W, H)
  ctx.stroke()

  // build volume map from ALL trades (not just visible window)
  const volMap = new Map<number, { buy: number; sell: number }>()
  for (const t of props.trades) {
    const key = roundTick(t.p, tick)
    const e = volMap.get(key) ?? { buy: 0, sell: 0 }
    if (t.d >= 0) e.buy += t.v
    else e.sell += t.v
    volMap.set(key, e)
  }

  let maxTotal = 1
  let hotKey = 0
  for (const [k, v] of volMap) {
    const tot = v.buy + v.sell
    if (tot > maxTotal) { maxTotal = tot; hotKey = k }
  }

  const barMaxW = PROFILE_W - PRICE_LABEL_W - 4
  const barH = Math.max(2, Math.floor((tick / pRange) * CHART_H * 0.85))

  for (const [price, vol] of volMap) {
    const y = pToY(price)
    if (y < -barH || y > CHART_H + barH) continue

    const total = vol.buy + vol.sell
    const isHot = price === hotKey
    const buyW = Math.round((vol.buy / maxTotal) * barMaxW)
    const sellW = Math.round((vol.sell / maxTotal) * barMaxW)

    // green = buy (left side of bar area)
    ctx.fillStyle = isHot ? 'rgba(61,214,140,0.85)' : 'rgba(61,214,140,0.55)'
    ctx.fillRect(CHART_W + 1, y - barH / 2, buyW, barH)

    // red = sell (stacked right of buy)
    ctx.fillStyle = isHot ? 'rgba(244,96,78,0.85)' : 'rgba(244,96,78,0.55)'
    ctx.fillRect(CHART_W + 1 + buyW, y - barH / 2, sellW, barH)

    // volume label
    if (total >= 5) {
      ctx.fillStyle = isHot ? '#E8A33D' : '#8FA0AE'
      ctx.font = isHot ? 'bold 9px monospace' : '9px monospace'
      ctx.textAlign = 'left'
      ctx.fillText(fmtVol(total), CHART_W + buyW + sellW + 3, y + 3)
    }

    // price label (far right)
    ctx.fillStyle = '#54616E'
    ctx.font = '8px monospace'
    ctx.textAlign = 'right'
    const digits = tick < 0.01 ? 3 : tick < 0.1 ? 2 : 1
    ctx.fillText(price.toFixed(digits), W - 2, y + 3)
  }

  // ── current price dashed line + badge ─────────────────────────────────────
  const cp = props.currentPrice
  if (cp != null) {
    const y = pToY(cp)
    if (y >= 0 && y <= CHART_H) {
      ctx.strokeStyle = '#E8A33D'
      ctx.lineWidth = 1
      ctx.setLineDash([4, 3])
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(CHART_W, y)
      ctx.stroke()
      ctx.setLineDash([])

      // badge background
      ctx.font = 'bold 9px monospace'
      const lbl = cp.toFixed(tick < 0.01 ? 3 : 2)
      const tw = ctx.measureText(lbl).width + 6
      ctx.fillStyle = '#E8A33D'
      ctx.fillRect(CHART_W - tw - 1, y - 8, tw + 1, 15)
      ctx.fillStyle = '#0C1117'
      ctx.textAlign = 'right'
      ctx.fillText(lbl, CHART_W - 4, y + 3)
    }
  }

  rafId = requestAnimationFrame(draw)
}

// ── lifecycle ──────────────────────────────────────────────────────────────

onMounted(() => { rafId = requestAnimationFrame(draw) })
onUnmounted(() => { cancelAnimationFrame(rafId) })
</script>

<template>
  <div class="chart-wrap">
    <!-- window controls -->
    <div class="ctrl-row">
      <span class="faint" style="font-size: 10px">窗口</span>
      <button
        v-for="m in [1, 2, 3, 5, 10]"
        :key="m"
        class="win-btn"
        :class="{ active: windowMin === m }"
        @click="windowMin = m"
      >
        {{ m }}m
      </button>
    </div>
    <canvas ref="canvas" class="chart-canvas" />
  </div>
</template>

<style scoped>
.chart-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ctrl-row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.win-btn {
  font-family: var(--mono);
  font-size: 10px;
  padding: 2px 7px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--muted);
  border-radius: 3px;
  cursor: pointer;
}
.win-btn.active {
  background: rgba(232, 163, 61, 0.12);
  border-color: var(--amber-dim, #8A6526);
  color: var(--amber);
}
.chart-canvas {
  width: 100%;
  height: 240px;
  border-radius: 4px;
  display: block;
}
</style>
