<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { NSpin } from 'naive-ui'
import { adviceApi } from '@/api/endpoints'
import type { AdviceHistoryItem, AdviceResult } from '@/api/types'
import { fmtTs } from '@/composables/format'
import { palette } from '@/theme'

const props = defineProps<{ symbol: string; range?: { start: string; end: string } | null }>()

const reasoning = ref('')
const phase = ref('')
const result = ref<AdviceResult | null>(null)
const error = ref<string | null>(null)
const running = ref(false)
const cached = ref(false)
const history = ref<AdviceHistoryItem[]>([])
const showHistory = ref(false)
let es: EventSource | null = null
let debounce: ReturnType<typeof setTimeout> | undefined

function stop() { es?.close(); es = null; running.value = false }

function connect(force: boolean) {
  stop()
  reasoning.value = ''; phase.value = ''; error.value = null
  if (force) { result.value = null; cached.value = false }
  running.value = true
  es = new EventSource(adviceApi.streamUrl(props.symbol, props.range?.start, props.range?.end, force))
  es.onmessage = (e) => {
    let ev: Record<string, unknown>
    try { ev = JSON.parse(e.data) } catch { return }
    const t = ev.type as string
    if (t === 'phase') {
      phase.value = (ev.detail as string) || phase.value
    } else if (t === 'delta') {
      if (ev.agent === 'advice') reasoning.value += (ev.text as string) || ''
    } else if (t === 'result') {
      result.value = ev.data as AdviceResult
      cached.value = Boolean(ev.cached)
      stop()
      loadLatest(false)   // 刷新历史列表
    } else if (t === 'error') {
      error.value = (ev.message as string) || '生成失败'
      stop()
    }
  }
  es.onerror = () => { if (running.value && !result.value) { error.value = '连接中断，请重试'; stop() } }
}

/** 打开/切换：先读库内最近结果+历史；有 fresh 缓存则展示不重算，否则 debounce 后再跑分析 */
async function loadLatest(autoRun = true) {
  const sym = props.symbol
  const d = await adviceApi.latest(sym)
  if (sym !== props.symbol) return   // 期间已切换，丢弃
  history.value = d.history || []
  if (d.latest) {
    result.value = d.latest.result
    cached.value = true
  }
  if (!autoRun) return
  // 实时且已有近 30min 结果 → 由后端 cache_hit 判定；这里若已有实时结果就不主动重算
  const hasFresh = d.latest?.is_realtime && withinTtl(d.latest.ts)
  if (hasFresh) return
  // 无 fresh：debounce 1.5s 再连 SSE（避免快速切换时连开多个 LLM）
  clearTimeout(debounce)
  debounce = setTimeout(() => { if (sym === props.symbol) connect(false) }, 1500)
}

function withinTtl(ts: string | null): boolean {
  if (!ts) return false
  return Date.now() - new Date(ts).getTime() <= 30 * 60 * 1000
}

function reanalyze() { clearTimeout(debounce); connect(true) }

function viewHistory(h: AdviceHistoryItem) {
  stop(); clearTimeout(debounce)
  result.value = h.result; cached.value = true; showHistory.value = false
}

function reset() {
  stop(); clearTimeout(debounce)
  result.value = null; reasoning.value = ''; error.value = ''; phase.value = ''
}

watch(() => props.symbol, () => { reset(); loadLatest(true) }, { immediate: true })
watch(() => props.range, () => { reset(); loadLatest(true) })
onUnmounted(() => { stop(); clearTimeout(debounce) })

const stanceColor = (s?: string) => (s === '偏多' ? palette.up : s === '偏空' ? palette.down : palette.muted)
const DIM_ICON: Record<string, string> = {
  技术: '📊', 资金: '💰', 期权: '🎯', 情绪: '🗣', 趋势: '📈',
}
</script>

<template>
  <div class="advice-panel">
    <div class="ap-head">
      <span class="col-title">🎯 当前走向 · 短线建议</span>
      <span v-if="result?.is_realtime === false" class="tag-faint">历史区间·复盘</span>
      <span v-else-if="result" class="tag-live">含实时</span>
      <span v-if="cached && result" class="tag-faint" title="来自最近的持久化结果，未重新消耗 token">缓存</span>
      <span class="grow" />
      <button v-if="history.length" class="ap-mini" :class="{ on: showHistory }" @click="showHistory = !showHistory">
        历史 {{ history.length }}
      </button>
      <button v-if="!running" class="ap-mini" title="忽略缓存，重新分析" @click="reanalyze">重新分析</button>
    </div>

    <!-- 历史(最多5条，点开读缓存) -->
    <div v-if="showHistory && history.length" class="hist-list">
      <div v-for="h in history" :key="h.id" class="hist-row" @click="viewHistory(h)">
        <span class="mono small" :style="{ color: stanceColor(h.stance || undefined) }">{{ h.stance || '—' }}</span>
        <span class="faint small">{{ fmtTs(h.ts) }}</span>
        <span v-if="h.is_realtime" class="faint small">· 实时</span>
      </div>
    </div>

    <div v-if="error" class="ap-err">{{ error }}</div>

    <!-- 结论头 -->
    <div v-if="result" class="ap-verdict">
      <span class="stance" :style="{ color: stanceColor(result.stance), borderColor: stanceColor(result.stance) }">
        {{ result.stance || '中性' }}
      </span>
      <span v-if="result.is_near_low" class="low-chip">疑似短期低点</span>
      <span v-if="result.confidence != null" class="conf">置信度 {{ result.confidence }}%</span>
    </div>
    <p v-if="result?.horizon" class="ap-horizon">{{ result.horizon }}</p>

    <div v-if="result" class="ap-grid">
      <div v-if="result.entry" class="ap-cell">
        <div class="cell-k">进场 / 加仓</div>
        <div class="cell-v">{{ result.entry }}</div>
      </div>
      <div v-if="result.exit" class="ap-cell">
        <div class="cell-k">止盈 / 止损</div>
        <div class="cell-v">{{ result.exit }}</div>
      </div>
    </div>

    <!-- 多维理论支撑 -->
    <div v-if="result?.thesis?.length" class="thesis">
      <div class="cell-k">多维理论支撑</div>
      <div v-for="(t, i) in result.thesis" :key="i" class="th-row">
        <span class="th-dim">{{ DIM_ICON[t.dim] || '•' }} {{ t.dim }}</span>
        <div class="th-body">
          <div class="th-point">{{ t.point }}</div>
          <div v-if="t.support" class="th-support">依据：{{ t.support }}</div>
        </div>
      </div>
    </div>

    <div v-if="result?.caveats" class="ap-caveat">⚠ {{ result.caveats }}</div>

    <!-- 流式推理过程（生成中） -->
    <div v-if="running" class="ap-stream">
      <div class="ap-phase"><n-spin :size="12" /> {{ phase || '生成中…' }}</div>
      <div v-if="reasoning" class="ap-reason">{{ reasoning }}</div>
    </div>
  </div>
</template>

<style scoped>
.advice-panel { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.ap-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.col-title { font-size: 12px; color: var(--amber); font-weight: 600; letter-spacing: .03em; }
.grow { flex: 1; }
.tag-live { font-size: 10px; color: var(--up); border: 1px solid var(--up); border-radius: 8px; padding: 0 6px; }
.tag-faint { font-size: 10px; color: var(--faint); border: 1px solid var(--line2); border-radius: 8px; padding: 0 6px; }
.ap-mini { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 5px; font-size: 11px; padding: 2px 8px; cursor: pointer; }
.ap-mini:hover, .ap-mini.on { border-color: var(--amber); color: var(--amber); }
.hist-list { border: 1px solid var(--line); border-radius: 6px; margin-bottom: 8px; max-height: 150px; overflow-y: auto; }
.hist-row { display: flex; align-items: center; gap: 10px; padding: 5px 9px; cursor: pointer; border-bottom: 1px solid var(--panel2); }
.hist-row:last-child { border-bottom: none; }
.hist-row:hover { background: var(--panel2); }
.mono { font-family: var(--mono); }
.ap-err { color: var(--down); font-size: 12px; padding: 6px 0; }
.ap-verdict { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.stance { font-size: 14px; font-weight: 700; border: 1px solid; border-radius: 6px; padding: 1px 10px; }
.low-chip { font-size: 11px; color: var(--up); background: rgba(61,214,140,.12); border-radius: 10px; padding: 1px 8px; }
.conf { font-size: 11px; font-family: var(--mono); color: var(--muted); }
.ap-horizon { font-size: 12.5px; color: var(--text); line-height: 1.55; margin: 4px 0 8px; }
.ap-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
@media (max-width: 520px) { .ap-grid { grid-template-columns: 1fr; } }
.ap-cell { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; }
.cell-k { font-size: 10px; color: var(--faint); letter-spacing: .04em; margin-bottom: 3px; }
.cell-v { font-size: 12px; color: var(--text); line-height: 1.5; }
.thesis { margin-top: 6px; }
.th-row { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }
.th-row:last-child { border-bottom: none; }
.th-dim { font-size: 11.5px; color: var(--amber); flex: none; width: 58px; }
.th-body { flex: 1; }
.th-point { font-size: 12px; color: var(--text); line-height: 1.5; }
.th-support { font-size: 11px; color: var(--faint); line-height: 1.5; margin-top: 2px; }
.ap-caveat { font-size: 11px; color: var(--faint); margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line); }
.ap-stream { margin-top: 8px; }
.ap-phase { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--amber); }
.ap-reason { font-size: 11.5px; color: var(--muted); line-height: 1.55; margin-top: 6px; white-space: pre-wrap; max-height: 220px; overflow-y: auto; }
</style>
