<script setup lang="ts">
import { onUnmounted, reactive, ref, watch } from 'vue'
import { NSpin } from 'naive-ui'
import { attributionApi, type AttributionHistoryItem } from '@/api/endpoints'
import { palette } from '@/theme'

const props = defineProps<{ symbol: string; range: { start: string; end: string } | null }>()
const emit = defineEmits<{ close: [] }>()

type Status = 'pending' | 'running' | 'done' | 'failed'
interface AgentState { key: string; label: string; status: Status; thinking: string }

const AGENTS: Array<{ key: string; label: string }> = [
  { key: 'gather', label: '采集线索' },
  { key: 'fundamental', label: '基本面 / 消息面' },
  { key: 'technical', label: '技术面' },
  { key: 'sentiment', label: '情绪资金面' },
  { key: 'critic', label: '质疑校验' },
  { key: 'synth', label: '综合归因' },
]

const agents = reactive<Record<string, AgentState>>({})
function reset() {
  for (const a of AGENTS) agents[a.key] = { key: a.key, label: a.label, status: 'pending', thinking: '' }
}

interface Cause { cause: string; direction: string; confidence: number; evidence?: string }
interface Result {
  primary?: Cause[]; secondary?: Cause[]; narrative?: string; confidence?: number
  caveats?: string; evidence_news?: Array<{ id: number; title: string }>
  price_stats?: { pct_change?: number; start_date?: string; end_date?: string }
  cached?: boolean; created_at?: string
}
const result = ref<Result | null>(null)
const error = ref<string | null>(null)
const running = ref(false)
const history = ref<AttributionHistoryItem[]>([])
const showHistory = ref(false)
let es: EventSource | null = null

function stop() { es?.close(); es = null; running.value = false }

async function loadHistory() {
  history.value = await attributionApi.history(props.symbol, 12)
}
function viewHistory(item: AttributionHistoryItem) {
  stop()
  for (const k in agents) agents[k].status = 'done'
  result.value = { ...(item.result as Result), cached: true, created_at: item.created_at }
}

function start(force = false) {
  if (!props.range) return
  stop(); reset(); result.value = null; error.value = null; running.value = true
  es = new EventSource(attributionApi.streamUrl(props.symbol, props.range.start, props.range.end, force))
  es.onmessage = (e) => {
    let ev: Record<string, unknown>
    try { ev = JSON.parse(e.data) } catch { return }
    const t = ev.type as string
    if (t === 'phase') {
      const a = agents[ev.agent as string]
      if (a) { a.status = (ev.status as Status) || a.status }
    } else if (t === 'delta') {
      const a = agents[ev.agent as string]
      if (a) { a.status = 'running'; a.thinking += (ev.text as string) || '' }
    } else if (t === 'result') {
      result.value = ev.data as Result
      for (const k in agents) if (agents[k].status !== 'done') agents[k].status = 'done'
      stop()
      loadHistory()
    } else if (t === 'error') {
      error.value = (ev.message as string) || '分析失败'
      stop()
    }
  }
  es.onerror = () => { if (running.value && !result.value) { error.value = '连接中断，请重试'; stop() } }
}

watch(() => props.range, () => { if (props.range) { start(); loadHistory() } }, { immediate: true })
watch(() => props.symbol, () => { loadHistory() })
onUnmounted(stop)

const dirColor = (d?: string) => (d === '利多' ? palette.up : d === '利空' ? palette.down : palette.muted)
const ICON: Record<Status, string> = { pending: '○', running: '', done: '✓', failed: '✗' }
</script>

<template>
  <div class="attr-panel">
    <div class="attr-head">
      <span class="col-title">🧠 多 Agent 价格变动归因</span>
      <span v-if="range" class="faint small mono">{{ range.start.slice(0, 10) }} → {{ range.end.slice(0, 10) }}</span>
      <button v-if="history.length" class="attr-mini" :class="{ on: showHistory }" @click="showHistory = !showHistory">历史 {{ history.length }}</button>
      <button v-if="!running" class="attr-mini" title="忽略缓存重新分析" @click="start(true)">重新分析</button>
      <button class="attr-close" @click="emit('close')">✕</button>
    </div>

    <!-- 历史归因(可回看，点开即读缓存) -->
    <div v-if="showHistory && history.length" class="hist-list">
      <div v-for="h in history" :key="h.id" class="hist-row" @click="viewHistory(h)">
        <span class="mono small">{{ h.start.slice(5, 10) }}→{{ h.end.slice(5, 10) }}</span>
        <span v-if="h.pct_change != null" class="mono small" :style="{ color: h.pct_change >= 0 ? palette.up : palette.down }">
          {{ h.pct_change >= 0 ? '+' : '' }}{{ h.pct_change }}%
        </span>
        <span class="faint small">{{ h.created_at.slice(0, 16).replace('T', ' ') }}</span>
      </div>
    </div>

    <div v-if="error" class="attr-err">{{ error }}</div>

    <!-- 各 Agent 思考与进度（默认展示） -->
    <div class="agents">
      <div v-for="a in AGENTS" :key="a.key" class="agent" :class="agents[a.key]?.status">
        <div class="a-head">
          <span class="a-node">
            <n-spin v-if="agents[a.key]?.status === 'running'" :size="11" />
            <span v-else class="ic">{{ ICON[agents[a.key]?.status || 'pending'] }}</span>
          </span>
          <span class="a-name">{{ a.label }}</span>
        </div>
        <div v-if="agents[a.key]?.thinking" class="a-think">{{ agents[a.key].thinking }}</div>
      </div>
    </div>

    <!-- 最终归因 -->
    <div v-if="result" class="attr-result">
      <div class="ar-title">
        归因结论
        <span v-if="result.confidence != null" class="conf-chip">整体置信度 {{ result.confidence }}%</span>
        <span v-if="result.cached" class="cache-chip" :title="result.created_at || ''">📁 历史缓存</span>
      </div>
      <p v-if="result.narrative" class="ar-narrative">{{ result.narrative }}</p>

      <div v-if="result.primary?.length" class="cause-block">
        <div class="cb-label">主要原因</div>
        <div v-for="(c, i) in result.primary" :key="i" class="cause">
          <div class="c-row">
            <span class="c-dir" :style="{ color: dirColor(c.direction) }">{{ c.direction || '中性' }}</span>
            <span class="c-cause">{{ c.cause }}</span>
            <span class="c-conf mono">{{ c.confidence }}%</span>
          </div>
          <div class="c-bar"><span :style="{ width: (c.confidence || 0) + '%', background: dirColor(c.direction) }" /></div>
          <div v-if="c.evidence" class="c-evi">证据：{{ c.evidence }}</div>
        </div>
      </div>

      <div v-if="result.secondary?.length" class="cause-block">
        <div class="cb-label">次要因素</div>
        <div v-for="(c, i) in result.secondary" :key="i" class="cause sec">
          <span class="c-dir" :style="{ color: dirColor(c.direction) }">{{ c.direction || '中性' }}</span>
          <span class="c-cause">{{ c.cause }}</span>
          <span class="c-conf mono">{{ c.confidence }}%</span>
        </div>
      </div>

      <div v-if="result.evidence_news?.length" class="cause-block">
        <div class="cb-label">参考新闻线索（已由系统 LLM 分析）</div>
        <div v-for="n in result.evidence_news" :key="n.id" class="evi-news">· {{ n.title }}</div>
      </div>

      <div v-if="result.caveats" class="ar-caveat">⚠ {{ result.caveats }}</div>
    </div>

    <div v-else-if="running && !error" class="faint small" style="text-align:center;padding:6px">
      Agent 协同分析中…
    </div>
  </div>
</template>

<style scoped>
.attr-panel { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; }
.attr-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.col-title { font-size: 12px; color: var(--amber); letter-spacing: .04em; font-weight: 600; }
.small { font-size: 11px; }
.mono { font-family: var(--mono); }
.attr-mini { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 5px; font-size: 11px; padding: 2px 7px; cursor: pointer; }
.attr-mini:hover, .attr-mini.on { border-color: var(--amber); color: var(--amber); }
.attr-close { margin-left: auto; background: transparent; border: none; color: var(--faint); cursor: pointer; }
.hist-list { border: 1px solid var(--line); border-radius: 6px; margin-bottom: 8px; max-height: 160px; overflow-y: auto; }
.hist-row { display: flex; align-items: center; gap: 10px; padding: 5px 9px; cursor: pointer; border-bottom: 1px solid var(--panel2); }
.hist-row:last-child { border-bottom: none; }
.hist-row:hover { background: var(--panel2); }
.cache-chip { font-size: 11px; color: var(--muted); background: var(--panel2); border: 1px solid var(--line2); border-radius: 10px; padding: 1px 8px; }
.attr-err { color: var(--down); font-size: 12px; padding: 6px 0; }

.agents { display: flex; flex-direction: column; gap: 6px; }
.agent { border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px; background: var(--panel2); }
.a-head { display: flex; align-items: center; gap: 7px; }
.a-node { width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; border-radius: 50%; border: 1.5px solid var(--line2); color: var(--faint); font-size: 11px; font-weight: 700; flex: none; }
.a-name { font-size: 12.5px; color: var(--text); }
.a-think { font-size: 11.5px; color: var(--muted); line-height: 1.55; margin-top: 5px; white-space: pre-wrap; padding-left: 25px; }
.agent.running { border-color: var(--amber); }
.agent.running .a-node { border-color: var(--amber); }
.agent.running .a-name { color: var(--amber); }
.agent.done .a-node { border-color: var(--up); color: var(--up); }
.agent.failed .a-node { border-color: var(--down); color: var(--down); }

.attr-result { margin-top: 12px; border-top: 1px dashed var(--line2); padding-top: 10px; }
.ar-title { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 8px; }
.conf-chip { font-size: 11px; font-family: var(--mono); color: var(--amber); background: rgba(232,163,61,.1); border: 1px solid var(--amber-dim); border-radius: 10px; padding: 1px 8px; }
.ar-narrative { font-size: 12.5px; color: var(--muted); line-height: 1.6; margin: 8px 0; }
.cause-block { margin-top: 10px; }
.cb-label { font-size: 11px; color: var(--faint); margin-bottom: 5px; letter-spacing: .04em; }
.cause { margin-bottom: 8px; }
.c-row { display: flex; align-items: center; gap: 8px; }
.c-dir { font-size: 11px; font-weight: 600; flex: none; }
.c-cause { font-size: 12.5px; color: var(--text); flex: 1; }
.c-conf { font-size: 11px; color: var(--muted); flex: none; }
.c-bar { height: 4px; background: var(--line); border-radius: 2px; margin-top: 4px; overflow: hidden; }
.c-bar span { display: block; height: 100%; border-radius: 2px; }
.c-evi { font-size: 11px; color: var(--faint); margin-top: 3px; padding-left: 2px; }
.cause.sec { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.evi-news { font-size: 11.5px; color: var(--muted); line-height: 1.6; }
.ar-caveat { font-size: 11px; color: var(--faint); margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); }
</style>
