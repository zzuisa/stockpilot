<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { NButton, NDataTable, NInput, NSelect, NSwitch, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { jobsApi, newsApi } from '@/api/endpoints'
import type { JobRun, LlmStatus, NewsBrief, NewsItem, NewsStats } from '@/api/types'
import { apiError } from '@/api/client'
import { fmtTs, sentLabel, sentType } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'
import ResearchPanel from '@/components/ResearchPanel.vue'

defineOptions({ name: 'NewsLLMView' })

const notify = useNotify()
const newsRun = ref<JobRun | null>(null)
const llm = ref<LlmStatus | null>(null)
const stats = ref<NewsStats | null>(null)
const items = ref<NewsItem[]>([])
const briefs = ref<NewsBrief[]>([])
const triggering = ref<string | null>(null)

const SENT_BRIEF: Record<string, { label: string; type: 'success' | 'error' | 'default' }> = {
  bullish: { label: '偏多', type: 'success' },
  bearish: { label: '偏空', type: 'error' },
  neutral: { label: '中性', type: 'default' },
}
function briefSent(s: string | null) {
  return SENT_BRIEF[s ?? 'neutral'] ?? SENT_BRIEF.neutral
}
function briefHtml(md: string | null): string {
  return (md ?? '').replace(/\n/g, '<br>')
}

function fmtInt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString('en-US')
}

// 过滤
const onlyScored = ref(true)
const symbol = ref('')
const source = ref<string>('')

const sourceOptions = [
  { label: '全部来源', value: '' },
  { label: 'Finnhub', value: 'finnhub' },
  { label: 'RSS', value: 'rss' },
  { label: 'AlphaVantage', value: 'alphavantage' },
]

function latestRun(runs: JobRun[], job: string): JobRun | null {
  let best: JobRun | null = null
  for (const r of runs) {
    if (r.job === job && (!best || r.started_at > best.started_at)) best = r
  }
  return best
}

async function load() {
  try {
    const [jobs, ls, st, list, br] = await Promise.all([
      jobsApi.list().catch(() => null),
      newsApi.llmStatus(),
      newsApi.stats(72),
      newsApi.list({
        limit: 120,
        scored: onlyScored.value ? '1' : undefined,
        symbol: symbol.value.trim() || undefined,
        source: source.value || undefined,
      }),
      newsApi.briefs({ limit: 30, symbol: symbol.value.trim() || undefined }),
    ])
    if (jobs) newsRun.value = latestRun(jobs.recent_runs, 'news')
    if (ls) llm.value = ls
    stats.value = st
    items.value = list
    briefs.value = br
  } catch (e) {
    notify.err(`加载失败: ${apiError(e)}`)
  }
}

const TRIGGER_MSG: Record<string, string> = {
  news: '新闻采集已触发',
  sentiment: 'LLM 分析已手动触发一轮',
  news_brief: '精华总结已触发生成',
}

async function trigger(job: 'news' | 'sentiment' | 'news_brief') {
  triggering.value = job
  try {
    await jobsApi.run(job)
    notify.ok(TRIGGER_MSG[job] ?? '已触发')
    setTimeout(load, 1500)
  } catch (e) {
    notify.err(`触发失败: ${apiError(e)}`)
  } finally {
    triggering.value = null
  }
}

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  load()
  timer = setInterval(load, 5000) // 5s 刷新：进度 + 新分析结果实时可见
})
onUnmounted(() => timer && clearInterval(timer))

// 情绪分布(5 档)
const SENT_ORDER = [2, 1, 0, -1, -2]
const distList = computed(() =>
  SENT_ORDER.map((s) => ({ s, n: stats.value?.by_sentiment?.[String(s)] ?? 0 })),
)

const TIER_LABEL: Record<number, string> = { 1: '权威', 2: '主流', 3: '一般' }

function runState(r: JobRun | null) {
  if (!r) return { type: 'default' as const, label: '未运行' }
  if (r.status === 'running') return { type: 'warning' as const, label: '运行中' }
  if (r.status === 'ok') return { type: 'success' as const, label: '成功' }
  if (r.status === 'skipped') return { type: 'default' as const, label: '跳过' }
  return { type: 'error' as const, label: '失败' }
}

const columns: DataTableColumns<NewsItem> = [
  {
    title: '时间',
    key: 'published',
    width: 132,
    className: 'mono muted',
    render: (n) => fmtTs(n.published ?? n.fetched_at),
  },
  {
    title: '标的',
    key: 'symbol',
    width: 64,
    render: (n) =>
      n.symbol
        ? h('span', { class: 'tag-amber' }, n.symbol)
        : h('span', { class: 'faint' }, '宏观'),
  },
  {
    title: '情绪',
    key: 'sentiment',
    width: 70,
    render: (n) =>
      n.sentiment == null
        ? h('span', { class: 'faint' }, '待分析')
        : h(NTag, { size: 'small', type: sentType(n.sentiment), bordered: false },
            { default: () => sentLabel(n.sentiment) }),
  },
  {
    title: '标题 / LLM 解读',
    key: 'title',
    render: (n) =>
      h('div', [
        h('a', {
          href: n.url, target: '_blank', rel: 'noopener',
          class: 'news-title',
        }, n.title || '(无标题)'),
        n.llm_reason && n.llm_reason !== 'fallback'
          ? h('div', { class: 'news-reason' }, `💬 ${n.llm_reason}`)
          : n.llm_reason === 'fallback'
            ? h('div', { class: 'faint news-reason' }, '（关键词兜底评分，未走 LLM）')
            : null,
      ]),
  },
  {
    title: '来源',
    key: 'source',
    width: 132,
    render: (n) => {
      const tier = n.source_tier ?? 0
      return h('div', { class: 'src-cell' }, [
        h('span', { class: 'muted' }, n.source_name || n.source),
        tier
          ? h(
              NTag,
              { size: 'tiny', bordered: false, type: tier === 1 ? 'success' : tier === 2 ? 'info' : 'default' },
              { default: () => TIER_LABEL[tier] ?? '' },
            )
          : null,
      ])
    },
  },
  {
    title: '质量',
    key: 'quality',
    width: 64,
    align: 'right',
    className: 'mono',
    render: (n) => (n.quality != null ? n.quality.toFixed(2) : '—'),
  },
]
</script>

<template>
  <div>
    <!-- 持续 LLM 工作器 + 实时进度 + token -->
    <panel-card title="LLM 工作器 · 持续分析">
      <template #header>
        <n-tag size="small" :type="llm?.running ? 'success' : 'default'" :bordered="false">
          {{ llm?.running ? (llm.phase === 'scoring' ? '分析中' : '运行中') : '未运行' }}
        </n-tag>
        <span class="grow" />
        <n-button size="tiny" secondary :loading="triggering === 'sentiment'" @click="trigger('sentiment')">
          手动触发一轮
        </n-button>
      </template>

      <!-- 实时进度 -->
      <div class="worker-progress">
        <span v-if="llm?.phase === 'scoring'" class="dot-live">●</span>
        <span :class="llm?.phase === 'scoring' ? 'job-progress' : 'muted'">
          {{ llm?.progress || '等待中…' }}
        </span>
      </div>

      <!-- token / 统计 -->
      <div class="stat-row" style="margin-top: 10px">
        <div class="stat"><div class="stat-k">累计打分</div><div class="stat-v up">{{ fmtInt(llm?.scored_total) }} 条</div></div>
        <div class="stat"><div class="stat-k">累计 Token</div><div class="stat-v amber">{{ fmtInt(llm?.tokens_total) }}</div></div>
        <div class="stat"><div class="stat-k">最近一轮</div><div class="stat-v">{{ fmtInt(llm?.scored_last) }} 条 / {{ fmtInt(llm?.tokens_last) }} tok</div></div>
        <div class="stat"><div class="stat-k">轮次 · 间隔</div><div class="stat-v">{{ llm?.calls ?? 0 }} · {{ llm?.interval ?? 15 }}s</div></div>
      </div>
      <div class="worker-meta">
        <span class="faint">模型 {{ llm?.model || '—' }}</span>
        <span class="faint">上次批次 {{ llm?.last_batch_at ? fmtTs(llm.last_batch_at) : '—' }}</span>
        <span v-if="llm?.error" class="down">⚠ {{ llm.error }}</span>
      </div>

      <!-- 配套：新闻采集 -->
      <div class="news-collect-row">
        <span class="job-name">新闻采集</span>
        <n-tag size="small" :type="runState(newsRun).type" :bordered="false">{{ runState(newsRun).label }}</n-tag>
        <span v-if="newsRun?.status === 'running' && newsRun.progress" class="job-progress" style="font-size: 11px">
          ⏳ {{ newsRun.progress }}
        </span>
        <span v-else class="faint" style="font-size: 11px">{{ newsRun ? fmtTs(newsRun.started_at) : '—' }}</span>
        <span class="grow" />
        <n-button size="tiny" secondary :loading="triggering === 'news'" @click="trigger('news')">采集</n-button>
      </div>
    </panel-card>

    <!-- 分析统计 -->
    <panel-card title="分析统计 · 近 72 小时">
      <div class="stat-row">
        <div class="stat"><div class="stat-k">新闻总数</div><div class="stat-v">{{ stats?.total ?? 0 }}</div></div>
        <div class="stat"><div class="stat-k">已分析</div><div class="stat-v up">{{ stats?.scored ?? 0 }}</div></div>
        <div class="stat"><div class="stat-k">待分析</div><div class="stat-v amber">{{ stats?.unscored ?? 0 }}</div></div>
        <div class="stat">
          <div class="stat-k">平均情绪</div>
          <div class="stat-v" :style="{ color: (stats?.avg ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }">
            {{ stats?.avg != null ? stats.avg.toFixed(2) : '—' }}
          </div>
        </div>
      </div>
      <div class="dist-row">
        <span class="faint" style="font-size: 12px">情绪分布:</span>
        <n-tag v-for="d in distList" :key="d.s" size="small" :type="sentType(d.s)" :bordered="false">
          {{ sentLabel(d.s) }} {{ d.n }}
        </n-tag>
      </div>
    </panel-card>

    <!-- 精华总结 · 投资判断 -->
    <panel-card title="新闻精华 · 投资判断">
      <template #header>
        <span class="grow" />
        <span class="faint" style="font-size: 12px">仅推送开启 news_auto 的标的</span>
        <n-button size="tiny" secondary :loading="triggering === 'news_brief'" @click="trigger('news_brief')">
          生成精华
        </n-button>
      </template>
      <div v-if="!briefs.length" class="faint" style="font-size: 12px; padding: 6px 2px">
        暂无精华。前往「分组」给关注的标的开启「新闻自动」后，采集时会自动生成并推送。
      </div>
      <div v-for="b in briefs" :key="b.id" class="brief-card">
        <div class="brief-head">
          <span class="tag-amber">{{ b.symbol }}</span>
          <n-tag size="small" :type="briefSent(b.sentiment).type" :bordered="false">
            {{ briefSent(b.sentiment).label }}
          </n-tag>
          <span class="faint" style="font-size: 11px">{{ b.item_count }} 条 · {{ fmtTs(b.ts) }}</span>
          <span class="grow" />
          <n-tag v-if="b.pushed" size="tiny" :bordered="false" type="info">已推送</n-tag>
        </div>
        <div v-if="b.judgment" class="brief-judg">💡 {{ b.judgment }}</div>
        <!-- summary_md 为系统生成的可信 HTML（<b>/<i> + 换行） -->
        <div class="brief-body" v-html="briefHtml(b.summary_md)" />
      </div>
    </panel-card>

    <!-- 个股深度研究 -->
    <research-panel />

    <!-- 实时分析结果 / 记录 -->
    <panel-card title="分析结果 / 记录">
      <template #header>
        <span class="grow" />
        <span class="faint" style="font-size: 12px">仅已分析</span>
        <n-switch v-model:value="onlyScored" size="small" @update:value="load" />
        <n-input v-model:value="symbol" placeholder="标的(如 NVDA)" size="small" style="width: 130px" @keyup.enter="load" />
        <n-select v-model:value="source" :options="sourceOptions" size="small" style="width: 130px" @update:value="load" />
        <n-button size="tiny" quaternary @click="load">⟳</n-button>
      </template>
      <n-data-table
        :columns="columns"
        :data="items"
        :bordered="false"
        size="small"
        :max-height="560"
        virtual-scroll
      />
    </panel-card>
  </div>
</template>

<style scoped>
.jobs-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 680px) {
  .jobs-grid {
    grid-template-columns: 1fr;
  }
}
.job-box {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
}
.job-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.job-name {
  font-size: 13px;
  font-weight: 500;
}
.job-head :deep(.n-button) {
  margin-left: auto;
}
.job-progress {
  margin-top: 6px;
  font-size: 12px;
  color: var(--amber);
  animation: blink 1.6s infinite;
}
.worker-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  min-height: 20px;
}
.dot-live {
  color: var(--up);
  font-size: 9px;
  animation: blink 1.2s infinite;
}
.worker-meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 11px;
  font-family: var(--mono);
}
.news-collect-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
@keyframes blink {
  50% {
    opacity: 0.55;
  }
}
.job-meta {
  margin-top: 6px;
  font-size: 11px;
  display: flex;
  gap: 8px;
  font-family: var(--mono);
}
.stat-row {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.stat-k {
  font-size: 11px;
  color: var(--faint);
}
.stat-v {
  font-family: var(--mono);
  font-size: 20px;
  margin-top: 2px;
}
.dist-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}
.ellip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
}
:deep(.news-title) {
  color: var(--text);
  text-decoration: none;
  font-size: 13px;
}
:deep(.news-title:hover) {
  color: var(--amber);
}
:deep(.news-reason) {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
  line-height: 1.4;
}
:deep(.src-cell) {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
:deep(.tag-amber) {
  font-family: var(--mono);
  font-size: 12px;
  background: rgba(232, 163, 61, 0.12);
  color: var(--amber);
  padding: 2px 7px;
  border-radius: 3px;
}
.brief-card {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: var(--panel2);
}
.brief-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.brief-judg {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text);
  line-height: 1.5;
}
.brief-body {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
.brief-body :deep(b) {
  color: var(--text);
}
</style>
