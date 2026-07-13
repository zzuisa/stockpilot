<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { NSpin } from 'naive-ui'
import { agentApi, intentsApi } from '@/api/endpoints'
import { useNotify } from '@/composables/useNotify'

// 买入/持仓场景下的 Agent 咨询面板：把 side/持仓上下文带进提问，
// 复用 supervisor 的 /agent/stream（多 Agent 委派），并展示待确认的 OrderIntent。
const props = defineProps<{ symbol: string; context?: string }>()
const notify = useNotify()

type Status = 'pending' | 'running' | 'done' | 'failed'
interface Phase { key: string; label: string; status: Status; thinking: string }

const LABELS: Record<string, string> = {
  supervisor: '首席投研', research: '研究子Agent', attribution: '价格归因',
  quant_advisor: '量化顾问', get_market_data: '市场快照', get_indicators: '技术指标',
  get_sentiment: '情绪面', get_strategy_status: '策略状态', thesis: '研究档案',
}

const phases = reactive<Phase[]>([])
const phaseMap = reactive<Record<string, Phase>>({})
function upsertPhase(key: string): Phase {
  let p = phaseMap[key]
  if (!p) { p = { key, label: LABELS[key] || key, status: 'running', thinking: '' }; phaseMap[key] = p; phases.push(p) }
  return p
}

const answer = ref('')
const error = ref<string | null>(null)
const running = ref(false)
const query = ref('')
const pending = ref<any[]>([])
let es: EventSource | null = null

const QUICKS = computed(() => [
  { label: '现在适合买吗', q: `结合当前估值/技术/情绪，现在${props.context || ''}是否合适？给情景区间与风险，不要买卖指令。` },
  { label: '合理买入区间', q: '给出合理的分批买入价格区间（bear/base/bull）及依据。' },
  { label: '主要风险', q: '当前买入这只票的主要风险有哪些？' },
  { label: '策略建议', q: '如果我要对这只票做量化策略，参数上你建议怎么设？' },
])

function stop() { es?.close(); es = null; running.value = false }
function reset() { phases.length = 0; for (const k in phaseMap) delete phaseMap[k]; answer.value = ''; error.value = null }

async function loadPending() {
  const all = await intentsApi.list('pending', 50)
  pending.value = (all as any[]).filter((i) => i.symbol === props.symbol.toUpperCase())
}

function ask(q?: string) {
  const text = (q ?? query.value).trim()
  if (!text) return
  query.value = text
  stop(); reset(); running.value = true
  es = new EventSource(agentApi.streamUrl(props.symbol, text))
  es.onmessage = (e) => {
    let ev: Record<string, unknown>
    try { ev = JSON.parse(e.data) } catch { return }
    const t = ev.type as string
    const agent = ev.agent as string
    if (t === 'phase') {
      const p = upsertPhase(agent); p.status = (ev.status as Status) || p.status
      if (ev.detail) p.thinking = p.thinking || String(ev.detail)
    } else if (t === 'delta') {
      if (agent === 'supervisor') answer.value += (ev.text as string) || ''
      else { const p = upsertPhase(agent); p.status = 'running'; p.thinking += (ev.text as string) || '' }
    } else if (t === 'result') {
      const d = ev.data as { answer?: string }
      if (d?.answer) answer.value = d.answer
      for (const p of phases) if (p.status !== 'done') p.status = 'done'
      stop(); loadPending()
    } else if (t === 'error') {
      error.value = (ev.message as string) || 'Agent 分析失败'; stop()
    }
  }
  es.onerror = () => { if (running.value && !answer.value) { error.value = '连接中断，请重试'; stop() } }
}

async function confirmIntent(id: string) {
  const r = await intentsApi.confirm(id)
  r?.ok ? notify.success(r.message || '已下单') : notify.warning(r?.message || '未执行')
  loadPending()
}
async function skipIntent(id: string) {
  await intentsApi.skip(id); notify.info('已忽略'); loadPending()
}

watch(() => props.symbol, () => { stop(); reset(); query.value = ''; loadPending() }, { immediate: true })
onUnmounted(stop)

const ICON: Record<Status, string> = { pending: '○', running: '', done: '✓', failed: '✗' }

function esc(s: string) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }
function inline(s: string) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`(.+?)`/g, '<code>$1</code>') }
function renderMd(md: string): string {
  const lines = (md || '').split('\n'); const out: string[] = []; let i = 0; let inList = false
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false } }
  while (i < lines.length) {
    const line = lines[i]
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) { closeList(); out.push(`<h${h[1].length + 3} class="md-h">${inline(h[2])}</h${h[1].length + 3}>`); i++; continue }
    if (/^\s*[-*]\s+/.test(line)) { if (!inList) { out.push('<ul class="md-ul">'); inList = true } out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ''))}</li>`); i++; continue }
    if (/^\s*>\s?/.test(line)) { closeList(); out.push(`<blockquote class="md-q">${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`); i++; continue }
    if (line.trim() === '') { closeList(); i++; continue }
    closeList(); out.push(`<p class="md-p">${inline(line)}</p>`); i++
  }
  closeList(); return out.join('')
}
const answerHtml = computed(() => renderMd(answer.value))
</script>

<template>
  <div class="ac-panel">
    <div class="ac-head">
      <span class="col-title">🤝 咨询 Agent</span>
      <span class="faint small">{{ symbol }}</span>
    </div>

    <!-- 待确认订单意向 -->
    <div v-if="pending.length" class="intents">
      <div v-for="it in pending" :key="it.id" class="intent-row">
        <span class="i-side" :class="it.side">{{ it.side === 'buy' ? '买' : '卖' }}</span>
        <span class="mono small">{{ it.quantity }} 股 · ≈€{{ it.order_value_eur }}</span>
        <span class="faint small ellip">{{ it.rule }}</span>
        <button class="i-ok" @click="confirmIntent(it.id)">确认</button>
        <button class="i-no" @click="skipIntent(it.id)">忽略</button>
      </div>
    </div>

    <div class="ac-input">
      <input v-model="query" class="ac-text" placeholder="就这次交易向 Agent 咨询…" :disabled="running" @keyup.enter="ask()" />
      <button class="ac-ask" :disabled="running || !query.trim()" @click="ask()">
        <n-spin v-if="running" :size="12" /><span v-else>咨询</span>
      </button>
    </div>
    <div class="ac-quicks">
      <button v-for="qk in QUICKS" :key="qk.label" class="ac-chip" :disabled="running" @click="ask(qk.q)">{{ qk.label }}</button>
    </div>

    <div v-if="error" class="ac-err">{{ error }}</div>

    <div v-if="phases.length" class="phases">
      <div v-for="p in phases" :key="p.key" class="phase" :class="p.status">
        <div class="p-head">
          <span class="p-node"><n-spin v-if="p.status === 'running'" :size="11" /><span v-else class="ic">{{ ICON[p.status] }}</span></span>
          <span class="p-name">{{ p.label }}</span>
        </div>
        <div v-if="p.thinking" class="p-think">{{ p.thinking }}</div>
      </div>
    </div>

    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-if="answerHtml" class="ac-body" v-html="answerHtml" />
  </div>
</template>

<style scoped>
.ac-panel { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; }
.ac-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.col-title { font-size: 12px; color: var(--amber); font-weight: 600; letter-spacing: .04em; }
.small { font-size: 11px; } .mono { font-family: var(--mono); }
.faint { color: var(--faint); } .ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.intents { display: flex; flex-direction: column; gap: 5px; margin-bottom: 8px; }
.intent-row { display: flex; align-items: center; gap: 8px; padding: 5px 8px; background: var(--panel2); border: 1px solid var(--amber-dim); border-radius: 6px; }
.i-side { font-size: 11px; font-weight: 700; border-radius: 4px; padding: 1px 6px; }
.i-side.buy { color: var(--up); border: 1px solid var(--up); } .i-side.sell { color: var(--down); border: 1px solid var(--down); }
.i-ok { margin-left: auto; background: var(--amber); border: none; color: #1a1206; border-radius: 5px; font-size: 11px; font-weight: 600; padding: 2px 10px; cursor: pointer; }
.i-no { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 5px; font-size: 11px; padding: 2px 9px; cursor: pointer; }
.ac-input { display: flex; gap: 6px; }
.ac-text { flex: 1; background: var(--panel2); border: 1px solid var(--line2); border-radius: 6px; color: var(--text); font-size: 12.5px; padding: 6px 9px; outline: none; }
.ac-text:focus { border-color: var(--amber); }
.ac-ask { background: var(--amber); border: none; color: #1a1206; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 14px; cursor: pointer; min-width: 52px; display: flex; align-items: center; justify-content: center; }
.ac-ask:disabled { opacity: .5; cursor: default; }
.ac-quicks { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.ac-chip { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 12px; font-size: 11px; padding: 2px 10px; cursor: pointer; }
.ac-chip:hover:not(:disabled) { border-color: var(--amber); color: var(--amber); }
.ac-chip:disabled { opacity: .5; cursor: default; }
.ac-err { color: var(--down); font-size: 12px; padding: 6px 0; }
.phases { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.phase { border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px; background: var(--panel2); }
.p-head { display: flex; align-items: center; gap: 7px; }
.p-node { width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; border-radius: 50%; border: 1.5px solid var(--line2); color: var(--faint); font-size: 11px; font-weight: 700; flex: none; }
.p-name { font-size: 12.5px; color: var(--text); }
.p-think { font-size: 11.5px; color: var(--muted); line-height: 1.55; margin-top: 5px; white-space: pre-wrap; padding-left: 25px; }
.phase.running { border-color: var(--amber); } .phase.running .p-node { border-color: var(--amber); } .phase.running .p-name { color: var(--amber); }
.phase.done .p-node { border-color: var(--up); color: var(--up); }
.ac-body { font-size: 12.5px; color: var(--muted); line-height: 1.65; margin-top: 12px; border-top: 1px dashed var(--line2); padding-top: 10px; }
.ac-body :deep(.md-h) { color: var(--text); font-size: 13px; margin: 12px 0 6px; }
.ac-body :deep(.md-p) { margin: 6px 0; }
.ac-body :deep(.md-ul) { margin: 6px 0; padding-left: 18px; }
.ac-body :deep(.md-q) { border-left: 2px solid var(--amber-dim); padding: 2px 0 2px 10px; margin: 8px 0; color: var(--faint); font-size: 11.5px; }
.ac-body :deep(code) { font-family: var(--mono); background: var(--panel2); padding: 0 4px; border-radius: 3px; font-size: 11.5px; }
</style>
