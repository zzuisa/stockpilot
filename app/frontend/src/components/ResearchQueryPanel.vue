<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { NSpin } from 'naive-ui'
import { researchQueryApi, type ResearchQueryHistoryItem } from '@/api/endpoints'

const props = defineProps<{ symbol: string }>()
const emit = defineEmits<{ close: [] }>()

type Status = 'pending' | 'running' | 'done' | 'failed'
interface Phase { key: string; label: string; status: Status; thinking: string }

// agent key → 展示名（研究/归因子 Agent 混用，未知键回退原文）
const LABELS: Record<string, string> = {
  intent: '意图识别', freshness: '新鲜度校验', research: '研究分析',
  quick_fact: '快问快答', gather: '采集线索', fundamental: '基本面 / 消息面',
  technical: '技术面', sentiment: '情绪资金面', critic: '质疑校验', synth: '综合归因',
}

const phases = reactive<Phase[]>([])
const phaseMap = reactive<Record<string, Phase>>({})
function upsertPhase(key: string): Phase {
  let p = phaseMap[key]
  if (!p) {
    p = { key, label: LABELS[key] || key, status: 'running', thinking: '' }
    phaseMap[key] = p
    phases.push(p)
  }
  return p
}

interface ResearchResult {
  template?: string; answer?: string
  market_data?: Record<string, any> | null
  freshness?: { flag?: boolean; note?: string; live_price?: number; blended_target?: number; divergence_pct?: number } | null
  intent?: { template?: string; note?: string }
  cached?: boolean; created_at?: string
}
const result = ref<ResearchResult | null>(null)
const error = ref<string | null>(null)
const running = ref(false)
const query = ref('')
const history = ref<ResearchQueryHistoryItem[]>([])
const showHistory = ref(false)
let es: EventSource | null = null

const QUICKS: Array<{ label: string; q: string }> = [
  { label: '估值区间', q: '给出合理投资区间（bear/base/bull）' },
  { label: '为什么涨跌', q: '最近价格为什么这样波动？' },
  { label: '深度体检', q: '对这只股票做一次深度基本面体检' },
  { label: '财报解读', q: '解读最近一次财报' },
  { label: '下次财报几号', q: '下次财报是几号？' },
]

function stop() { es?.close(); es = null; running.value = false }
function resetStream() {
  phases.length = 0
  for (const k in phaseMap) delete phaseMap[k]
  result.value = null; error.value = null
}

async function loadHistory() {
  history.value = await researchQueryApi.history(props.symbol, 12)
}
function viewHistory(item: ResearchQueryHistoryItem) {
  stop(); resetStream()
  query.value = item.query
  result.value = { ...(item.result as ResearchResult), cached: true, created_at: item.created_at }
}

function ask(q?: string, force = false) {
  const text = (q ?? query.value).trim()
  if (!text) return
  query.value = text
  stop(); resetStream(); running.value = true
  es = new EventSource(researchQueryApi.streamUrl(props.symbol, text, force))
  es.onmessage = (e) => {
    let ev: Record<string, unknown>
    try { ev = JSON.parse(e.data) } catch { return }
    const t = ev.type as string
    const agent = ev.agent as string
    if (t === 'phase') {
      if (agent === 'start') return
      const p = upsertPhase(agent)
      p.status = (ev.status as Status) || p.status
      if (ev.detail) p.thinking = p.thinking || String(ev.detail)
    } else if (t === 'delta') {
      const p = upsertPhase(agent)
      p.status = 'running'; p.thinking += (ev.text as string) || ''
    } else if (t === 'result') {
      result.value = ev.data as ResearchResult
      for (const p of phases) if (p.status !== 'done') p.status = 'done'
      stop(); loadHistory()
    } else if (t === 'error') {
      error.value = (ev.message as string) || '分析失败'; stop()
    }
  }
  es.onerror = () => { if (running.value && !result.value) { error.value = '连接中断，请重试'; stop() } }
}

watch(() => props.symbol, () => { stop(); resetStream(); query.value = ''; loadHistory() }, { immediate: true })
onUnmounted(stop)

const ICON: Record<Status, string> = { pending: '○', running: '', done: '✓', failed: '✗' }

// ── 极简 Markdown 渲染（零新依赖：标题/加粗/列表/引用/管道表格）──────────────
function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function inline(s: string): string {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`(.+?)`/g, '<code>$1</code>')
}
function renderMd(md: string): string {
  const lines = (md || '').split('\n')
  const out: string[] = []
  let i = 0
  let inList = false
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false } }
  while (i < lines.length) {
    const line = lines[i]
    // 管道表格
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      closeList()
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const head = cells(line)
      out.push('<table class="md-tbl"><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>')
      i += 2
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        out.push('<tr>' + cells(lines[i]).map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>'); i++
      }
      out.push('</tbody></table>'); continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) { closeList(); const lvl = h[1].length; out.push(`<h${lvl + 2} class="md-h">${inline(h[2])}</h${lvl + 2}>`); i++; continue }
    if (/^\s*[-*]\s+/.test(line)) { if (!inList) { out.push('<ul class="md-ul">'); inList = true } out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ''))}</li>`); i++; continue }
    if (/^\s*>\s?/.test(line)) { closeList(); out.push(`<blockquote class="md-q">${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`); i++; continue }
    if (line.trim() === '') { closeList(); i++; continue }
    closeList(); out.push(`<p class="md-p">${inline(line)}</p>`); i++
  }
  closeList()
  return out.join('')
}
const answerHtml = computed(() => renderMd(result.value?.answer || ''))

const md = computed(() => result.value?.market_data || null)
const facts = computed(() => {
  const m = md.value
  if (!m) return [] as Array<{ k: string; v: string }>
  const f = m.fundamentals || {}, a = m.analyst || {}, qm = m.quote || {}
  const fmt = (x: any, s = '') => (x == null ? '—' : `${x}${s}`)
  return [
    { k: '现价', v: fmt(qm.live_price) },
    { k: '52周', v: `${fmt(qm.low52)} ~ ${fmt(qm.high52)}` },
    { k: '预期PE', v: fmt(f.forward_pe) },
    { k: '历史PE', v: fmt(f.trailing_pe) },
    { k: '目标均价', v: a.target_mean == null ? '—' : `${a.target_mean}（${fmt(a.target_low)}~${fmt(a.target_high)}）` },
    { k: '财报日', v: fmt(m.earnings_date) },
  ].filter((x) => x.v !== '—')
})

// 归因(attribution)结果：无 answer 字段，用 primary/narrative/evidence_news 渲染
const attr = computed(() => {
  const r = result.value as any
  if (r && (Array.isArray(r.primary) || r.narrative)) return r
  return null
})
function dirClass(d?: string) {
  if (!d) return 'neutral'
  if (d.includes('多')) return 'up'
  if (d.includes('空')) return 'down'
  return 'neutral'
}
</script>

<template>
  <div class="rq-panel">
    <div class="rq-head">
      <span class="col-title">🔬 股票研究 Agent</span>
      <span class="faint small">{{ symbol }}</span>
      <button v-if="history.length" class="rq-mini" :class="{ on: showHistory }" @click="showHistory = !showHistory">历史 {{ history.length }}</button>
      <button v-if="!running && result" class="rq-mini" title="忽略缓存重新分析" @click="ask(query, true)">重新分析</button>
      <button class="rq-close" @click="emit('close')">✕</button>
    </div>

    <div class="rq-input">
      <input v-model="query" class="rq-text" placeholder="就该标的自由提问，如「合理投资区间」「为什么最近跌」…"
             :disabled="running" @keyup.enter="ask()" />
      <button class="rq-ask" :disabled="running || !query.trim()" @click="ask()">
        <n-spin v-if="running" :size="12" /><span v-else>提问</span>
      </button>
    </div>
    <div class="rq-quicks">
      <button v-for="qk in QUICKS" :key="qk.label" class="rq-chip" :disabled="running" @click="ask(qk.q)">{{ qk.label }}</button>
    </div>

    <!-- 历史 -->
    <div v-if="showHistory && history.length" class="hist-list">
      <div v-for="h in history" :key="h.id" class="hist-row" @click="viewHistory(h)">
        <span class="tag">{{ LABELS[h.template || ''] || h.template }}</span>
        <span class="faint small ellip">{{ h.query }}</span>
        <span class="faint small mono">{{ h.created_at.slice(5, 16).replace('T', ' ') }}</span>
      </div>
    </div>

    <div v-if="error" class="rq-err">{{ error }}</div>

    <!-- 阶段 / 思考流 -->
    <div v-if="phases.length" class="phases">
      <div v-for="p in phases" :key="p.key" class="phase" :class="p.status">
        <div class="p-head">
          <span class="p-node">
            <n-spin v-if="p.status === 'running'" :size="11" />
            <span v-else class="ic">{{ ICON[p.status] }}</span>
          </span>
          <span class="p-name">{{ p.label }}</span>
        </div>
        <div v-if="p.thinking" class="p-think">{{ p.thinking }}</div>
      </div>
    </div>

    <!-- 结果 -->
    <div v-if="result" class="rq-result">
      <div class="rr-title">
        分析结论
        <span v-if="result.intent?.template" class="tag">{{ LABELS[result.intent.template] || result.intent.template }}</span>
        <span v-if="result.cached" class="cache-chip" :title="result.created_at || ''">📁 历史缓存</span>
      </div>

      <div v-if="result.freshness?.flag" class="fresh-warn">
        ⚠ {{ result.freshness.note }}
        <span class="mono small">（现价 {{ result.freshness.live_price }} vs 均价目标 {{ result.freshness.blended_target }}，背离 {{ result.freshness.divergence_pct }}%）</span>
      </div>

      <div v-if="facts.length" class="facts">
        <div v-for="f in facts" :key="f.k" class="fact"><span class="f-k">{{ f.k }}</span><span class="f-v mono">{{ f.v }}</span></div>
      </div>

      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-if="answerHtml" class="rr-body" v-html="answerHtml" />

      <!-- 归因结果（attribution 模板）-->
      <div v-if="attr" class="attr">
        <div v-for="(p, i) in (attr.primary || [])" :key="'p' + i" class="attr-cause primary">
          <div class="ac-head">
            <span class="ac-dir" :class="dirClass(p.direction)">{{ p.direction || '中性' }}</span>
            <span class="ac-cause">{{ p.cause }}</span>
            <span class="ac-conf">{{ p.confidence }}%</span>
          </div>
          <div v-if="p.evidence" class="ac-ev">{{ p.evidence }}</div>
        </div>
        <div v-for="(p, i) in (attr.secondary || [])" :key="'s' + i" class="attr-cause secondary">
          <div class="ac-head">
            <span class="ac-dir" :class="dirClass(p.direction)">{{ p.direction || '中性' }}</span>
            <span class="ac-cause">{{ p.cause }}</span>
            <span class="ac-conf">{{ p.confidence }}%</span>
          </div>
          <div v-if="p.evidence" class="ac-ev">{{ p.evidence }}</div>
        </div>

        <p v-if="attr.narrative" class="attr-narr">{{ attr.narrative }}</p>
        <p v-if="attr.caveats" class="attr-caveat">⚠ {{ attr.caveats }}</p>

        <!-- 使用的数据：新闻来源(链接) -->
        <div v-if="attr.evidence_news && attr.evidence_news.length" class="attr-src">
          <div class="as-title">📎 引用数据 · 新闻来源（{{ attr.evidence_news.length }}）</div>
          <ul class="as-list">
            <li v-for="n in attr.evidence_news" :key="n.id">
              <a v-if="n.url" :href="n.url" target="_blank" rel="noopener">{{ n.title }}</a>
              <span v-else>{{ n.title }}</span>
              <span v-if="n.date" class="as-date mono">{{ n.date }}</span>
              <span v-if="n.online" class="as-online">联网</span>
            </li>
          </ul>
        </div>

        <!-- 价格数据摘要 -->
        <div v-if="attr.price_stats" class="attr-ps mono small">
          📊 价格数据：{{ attr.price_stats.n }} 点<template v-if="attr.price_stats.n >= 2"> ·
            {{ attr.price_stats.start_date }}→{{ attr.price_stats.end_date }} ·
            {{ attr.price_stats.start_close }}→{{ attr.price_stats.end_close }}（{{ attr.price_stats.pct_change }}%）</template><template v-else>（本地数据不足，已结合联网新闻分析）</template>
        </div>
      </div>
    </div>

    <div v-else-if="running && !error && !phases.length" class="faint small" style="text-align:center;padding:6px">
      研究 Agent 启动中…
    </div>
  </div>
</template>

<style scoped>
.rq-panel { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; }
.rq-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.col-title { font-size: 12px; color: var(--amber); letter-spacing: .04em; font-weight: 600; }
.small { font-size: 11px; }
.mono { font-family: var(--mono); }
.ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rq-mini { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 5px; font-size: 11px; padding: 2px 7px; cursor: pointer; }
.rq-mini:hover, .rq-mini.on { border-color: var(--amber); color: var(--amber); }
.rq-close { margin-left: auto; background: transparent; border: none; color: var(--faint); cursor: pointer; }

.rq-input { display: flex; gap: 6px; }
.rq-text { flex: 1; background: var(--panel2); border: 1px solid var(--line2); border-radius: 6px; color: var(--text); font-size: 12.5px; padding: 6px 9px; outline: none; }
.rq-text:focus { border-color: var(--amber); }
.rq-ask { background: var(--amber); border: none; color: #1a1206; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 14px; cursor: pointer; min-width: 52px; display: flex; align-items: center; justify-content: center; }
.rq-ask:disabled { opacity: .5; cursor: default; }
.rq-quicks { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.rq-chip { background: transparent; border: 1px solid var(--line2); color: var(--muted); border-radius: 12px; font-size: 11px; padding: 2px 10px; cursor: pointer; }
.rq-chip:hover:not(:disabled) { border-color: var(--amber); color: var(--amber); }
.rq-chip:disabled { opacity: .5; cursor: default; }

.hist-list { border: 1px solid var(--line); border-radius: 6px; margin: 8px 0; max-height: 170px; overflow-y: auto; }
.hist-row { display: flex; align-items: center; gap: 8px; padding: 5px 9px; cursor: pointer; border-bottom: 1px solid var(--panel2); }
.hist-row:last-child { border-bottom: none; }
.hist-row:hover { background: var(--panel2); }
.tag { font-size: 10.5px; color: var(--amber); background: rgba(232,163,61,.1); border: 1px solid var(--amber-dim); border-radius: 9px; padding: 0 7px; flex: none; }
.cache-chip { font-size: 11px; color: var(--muted); background: var(--panel2); border: 1px solid var(--line2); border-radius: 10px; padding: 1px 8px; }
.rq-err { color: var(--down); font-size: 12px; padding: 6px 0; }

/* 归因结果 */
.attr { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.attr-cause { border: 1px solid var(--line2); border-radius: 6px; padding: 7px 9px; background: var(--panel2); }
.attr-cause.secondary { opacity: .85; }
.ac-head { display: flex; align-items: center; gap: 8px; }
.ac-dir { font-size: 11px; font-weight: 600; border-radius: 5px; padding: 1px 7px; flex: none; }
.ac-dir.up { color: var(--up); border: 1px solid var(--up); }
.ac-dir.down { color: var(--down); border: 1px solid var(--down); }
.ac-dir.neutral { color: var(--muted); border: 1px solid var(--line2); }
.ac-cause { font-size: 13px; font-weight: 600; color: var(--text); flex: 1; }
.ac-conf { font-size: 11px; color: var(--amber); flex: none; }
.ac-ev { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.5; }
.attr-narr { font-size: 12.5px; color: var(--text); line-height: 1.7; margin: 4px 0 0; }
.attr-caveat { font-size: 11.5px; color: var(--amber); margin: 0; }
.attr-src { border-top: 1px dashed var(--line2); padding-top: 7px; }
.as-title { font-size: 11px; color: var(--muted); letter-spacing: .03em; margin-bottom: 5px; }
.as-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.as-list li { font-size: 12px; line-height: 1.4; }
.as-list a { color: var(--amber); text-decoration: none; }
.as-list a:hover { text-decoration: underline; }
.as-date { color: var(--faint); font-size: 10.5px; margin-left: 6px; }
.as-online { color: var(--up); font-size: 10px; border: 1px solid var(--up); border-radius: 4px; padding: 0 4px; margin-left: 6px; }
.attr-ps { color: var(--faint); border-top: 1px dashed var(--line2); padding-top: 6px; }

.phases { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.phase { border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px; background: var(--panel2); }
.p-head { display: flex; align-items: center; gap: 7px; }
.p-node { width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; border-radius: 50%; border: 1.5px solid var(--line2); color: var(--faint); font-size: 11px; font-weight: 700; flex: none; }
.p-name { font-size: 12.5px; color: var(--text); }
.p-think { font-size: 11.5px; color: var(--muted); line-height: 1.55; margin-top: 5px; white-space: pre-wrap; padding-left: 25px; }
.phase.running { border-color: var(--amber); }
.phase.running .p-node { border-color: var(--amber); }
.phase.running .p-name { color: var(--amber); }
.phase.done .p-node { border-color: var(--up); color: var(--up); }
.phase.failed .p-node { border-color: var(--down); color: var(--down); }

.rq-result { margin-top: 12px; border-top: 1px dashed var(--line2); padding-top: 10px; }
.rr-title { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 8px; }
.fresh-warn { margin-top: 8px; font-size: 11.5px; color: var(--amber); background: rgba(232,163,61,.08); border: 1px solid var(--amber-dim); border-radius: 6px; padding: 6px 9px; line-height: 1.5; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 10px 0; padding: 8px 10px; background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; }
.fact { display: flex; gap: 6px; align-items: baseline; }
.f-k { font-size: 11px; color: var(--faint); }
.f-v { font-size: 12px; color: var(--text); }

.rr-body { font-size: 12.5px; color: var(--muted); line-height: 1.65; }
.rr-body :deep(.md-h) { color: var(--text); font-size: 13px; margin: 12px 0 6px; }
.rr-body :deep(.md-p) { margin: 6px 0; }
.rr-body :deep(.md-ul) { margin: 6px 0; padding-left: 18px; }
.rr-body :deep(.md-q) { border-left: 2px solid var(--amber-dim); padding: 2px 0 2px 10px; margin: 8px 0; color: var(--faint); font-size: 11.5px; }
.rr-body :deep(.md-tbl) { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px; }
.rr-body :deep(.md-tbl th), .rr-body :deep(.md-tbl td) { border: 1px solid var(--line2); padding: 4px 8px; text-align: left; }
.rr-body :deep(.md-tbl th) { background: var(--panel2); color: var(--text); }
.rr-body :deep(code) { font-family: var(--mono); background: var(--panel2); padding: 0 4px; border-radius: 3px; font-size: 11.5px; }
</style>
