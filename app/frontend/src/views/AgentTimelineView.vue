<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NSpin } from 'naive-ui'
import { agentApi } from '@/api/endpoints'

// Agent 运行记录：手动 + 托管所有思考过程与操作，按时间轴查看。
interface RunSummary {
  id: number; ts: string; symbol: string; mode: string
  trigger: string; outcome: any; n_events: number; has_decision: boolean
}
interface TLEvent {
  t: string; kind: string; agent?: string; detail?: string; text?: string
  tool?: string; args?: any; result?: string
  intent_id?: string; action?: string; reason?: string; value_eur?: number
}
interface RunDetail {
  id: number; ts: string; symbol: string; mode: string; trigger: string
  timeline: TLEvent[]; tool_calls: any[]; decision: any; outcome: any
}

const runs = ref<RunSummary[]>([])
const loading = ref(true)
const symbolFilter = ref('')
const modeFilter = ref('')          // '' | interactive | autonomy
const selected = ref<RunDetail | null>(null)
const loadingRun = ref(false)

async function loadRuns() {
  loading.value = true
  try {
    runs.value = await agentApi.history(symbolFilter.value || undefined, modeFilter.value || undefined, 50)
  } finally {
    loading.value = false
  }
}
async function openRun(id: number) {
  loadingRun.value = true
  try { selected.value = await agentApi.run(id) } finally { loadingRun.value = false }
}

onMounted(loadRuns)

const modeLabel = (m: string) => (m === 'autonomy' ? '托管' : '手动')
function hhmmss(iso: string) {
  try { return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false }) } catch { return iso?.slice(11, 19) || '' }
}
function dt(iso: string) {
  try { return new Date(iso).toLocaleString('zh-CN', { hour12: false }).slice(5) } catch { return iso?.slice(5, 16) || '' }
}

const KIND: Record<string, { icon: string; label: string }> = {
  start: { icon: '▶', label: '规划' },
  thinking: { icon: '💭', label: '思考' },
  tool: { icon: '🔧', label: '调用工具' },
  decision: { icon: '⚡', label: '决策' },
  answer: { icon: '✓', label: '结论' },
  exec: { icon: '💶', label: '执行' },
}
const kindOf = (k: string) => KIND[k] || { icon: '•', label: k }

const outcomeSummary = computed(() => (r: RunSummary) => {
  const o = r.outcome || {}
  const bits: string[] = []
  if (o.n_tools != null) bits.push(`${o.n_tools} 次调用`)
  if (o.n_decisions) bits.push(`${o.n_decisions} 决策`)
  if (o.executed?.length) bits.push(`执行 ${o.executed.length}`)
  if (o.escalated?.length) bits.push(`升级 ${o.escalated.length}`)
  return bits.join(' · ')
})
</script>

<template>
  <div class="tl-view">
    <div class="tl-head">
      <h2 class="title">🕓 Agent 记录</h2>
      <span class="muted small">手动咨询与托管自主的思考过程与操作，按时间轴留档</span>
    </div>

    <div class="filters">
      <input v-model="symbolFilter" class="fx" placeholder="标的（可空）" @keyup.enter="loadRuns" />
      <select v-model="modeFilter" class="fx" @change="loadRuns">
        <option value="">全部模式</option>
        <option value="interactive">手动</option>
        <option value="autonomy">托管</option>
      </select>
      <button class="fx-btn" @click="loadRuns">刷新</button>
    </div>

    <div class="tl-body">
      <!-- 运行列表 -->
      <div class="run-list">
        <div v-if="loading" class="muted small pad"><n-spin :size="14" /> 加载中…</div>
        <div v-else-if="!runs.length" class="muted small pad">暂无记录。去持仓页「咨询 Agent」或在设置页开启托管后即会留档。</div>
        <div v-for="r in runs" :key="r.id" class="run-row" :class="{ on: selected?.id === r.id }" @click="openRun(r.id)">
          <div class="rr-top">
            <span class="mode" :class="r.mode">{{ modeLabel(r.mode) }}</span>
            <span class="sym">{{ r.symbol }}</span>
            <span class="faint small mono">{{ dt(r.ts) }}</span>
          </div>
          <div class="rr-trig ellip">{{ r.trigger }}</div>
          <div class="rr-meta faint small">
            {{ outcomeSummary(r) }}<span v-if="r.has_decision" class="dec-dot">⚡有决策</span>
          </div>
        </div>
      </div>

      <!-- 时间轴详情 -->
      <div class="run-detail">
        <div v-if="loadingRun" class="muted small pad"><n-spin :size="14" /> 加载时间轴…</div>
        <div v-else-if="!selected" class="muted small pad">← 选择一条记录查看完整时间轴</div>
        <template v-else>
          <div class="rd-head">
            <span class="mode" :class="selected.mode">{{ modeLabel(selected.mode) }}</span>
            <span class="sym">{{ selected.symbol }}</span>
            <span class="faint small mono">{{ dt(selected.ts) }}</span>
          </div>
          <div class="rd-trig">{{ selected.trigger }}</div>

          <div class="timeline">
            <div v-for="(ev, i) in selected.timeline" :key="i" class="tl-ev" :class="ev.kind">
              <div class="ev-rail"><span class="ev-dot">{{ kindOf(ev.kind).icon }}</span></div>
              <div class="ev-body">
                <div class="ev-head">
                  <span class="ev-kind">{{ kindOf(ev.kind).label }}</span>
                  <span v-if="ev.tool" class="ev-tool mono">{{ ev.tool }}</span>
                  <span v-if="ev.action" class="ev-action" :class="ev.action">{{ ev.action === 'executed' ? '已执行' : '升级人工' }}</span>
                  <span class="faint small mono ev-time">{{ hhmmss(ev.t) }}</span>
                </div>
                <div v-if="ev.detail" class="ev-detail">{{ ev.detail }}</div>
                <div v-if="ev.text" class="ev-text">{{ ev.text }}</div>
                <div v-if="ev.args && Object.keys(ev.args).length" class="ev-args mono">参数 {{ JSON.stringify(ev.args) }}</div>
                <div v-if="ev.result" class="ev-result mono">→ {{ ev.result }}</div>
                <div v-if="ev.reason" class="ev-reason">原因：{{ ev.reason }}</div>
                <div v-if="ev.value_eur != null" class="faint small mono">≈€{{ ev.value_eur }}<span v-if="ev.intent_id"> · {{ String(ev.intent_id).slice(0, 8) }}</span></div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tl-view { padding: 8px 4px; }
.tl-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.title { font-size: 16px; color: var(--text); margin: 0; }
.muted { color: var(--muted); } .faint { color: var(--faint); } .small { font-size: 11px; }
.mono { font-family: var(--mono); } .pad { padding: 14px; }
.ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.filters { display: flex; gap: 8px; margin-bottom: 12px; }
.fx { background: var(--panel2); border: 1px solid var(--line2); border-radius: 6px; color: var(--text); font-size: 12.5px; padding: 5px 9px; outline: none; }
.fx:focus { border-color: var(--amber); }
.fx-btn { background: var(--amber); border: none; color: #1a1206; border-radius: 6px; font-weight: 600; font-size: 12px; padding: 0 14px; cursor: pointer; }
.tl-body { display: grid; grid-template-columns: minmax(240px, 340px) 1fr; gap: 14px; align-items: start; }
@media (max-width: 720px) { .tl-body { grid-template-columns: 1fr; } }

.run-list { border: 1px solid var(--line); border-radius: 8px; max-height: 72vh; overflow-y: auto; }
.run-row { padding: 9px 11px; border-bottom: 1px solid var(--panel2); cursor: pointer; }
.run-row:hover { background: var(--panel2); }
.run-row.on { background: var(--panel2); border-left: 2px solid var(--amber); }
.rr-top { display: flex; align-items: center; gap: 8px; }
.mode { font-size: 10.5px; font-weight: 700; border-radius: 5px; padding: 1px 7px; flex: none; }
.mode.autonomy { color: var(--amber); border: 1px solid var(--amber); }
.mode.interactive { color: var(--muted); border: 1px solid var(--line2); }
.sym { font-size: 13px; font-weight: 600; color: var(--text); }
.rr-trig { font-size: 12px; color: var(--muted); margin: 4px 0; line-height: 1.4; }
.rr-meta { display: flex; gap: 8px; }
.dec-dot { color: var(--amber); }

.run-detail { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; min-height: 200px; }
.rd-head { display: flex; align-items: center; gap: 10px; }
.rd-trig { font-size: 12.5px; color: var(--muted); margin: 8px 0 14px; line-height: 1.5; border-bottom: 1px dashed var(--line2); padding-bottom: 10px; }

.timeline { display: flex; flex-direction: column; }
.tl-ev { display: flex; gap: 10px; }
.ev-rail { display: flex; flex-direction: column; align-items: center; flex: none; }
.ev-dot { width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: var(--panel2); border: 1px solid var(--line2); font-size: 12px; }
.ev-rail::after { content: ''; flex: 1; width: 1px; background: var(--line2); margin: 2px 0; }
.tl-ev:last-child .ev-rail::after { display: none; }
.ev-body { padding-bottom: 14px; min-width: 0; flex: 1; }
.ev-head { display: flex; align-items: center; gap: 8px; }
.ev-kind { font-size: 12px; font-weight: 600; color: var(--text); }
.ev-tool { font-size: 11.5px; color: var(--amber); }
.ev-action { font-size: 10.5px; font-weight: 700; border-radius: 4px; padding: 0 6px; }
.ev-action.executed { color: var(--up); border: 1px solid var(--up); }
.ev-action.escalate { color: var(--down); border: 1px solid var(--down); }
.ev-time { margin-left: auto; }
.ev-detail { font-size: 12px; color: var(--muted); margin-top: 3px; }
.ev-text { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.55; white-space: pre-wrap; }
.tl-ev.answer .ev-text { color: var(--text); }
.ev-args, .ev-result { font-size: 11px; color: var(--faint); margin-top: 3px; word-break: break-all; }
.ev-result { color: var(--muted); }
.ev-reason { font-size: 11.5px; color: var(--amber); margin-top: 3px; }
.tl-ev.decision .ev-dot { border-color: var(--amber); }
.tl-ev.exec .ev-dot { border-color: var(--amber); }
.tl-ev.answer .ev-dot { border-color: var(--up); color: var(--up); }
</style>
