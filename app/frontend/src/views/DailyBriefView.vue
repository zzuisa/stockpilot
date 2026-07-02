<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NSelect } from 'naive-ui'
import { researchApi, t212Api } from '@/api/endpoints'
import type { DailyBriefData } from '@/api/types'
import { apiError } from '@/api/client'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'
import ProcessFlow, { type FlowStep } from '@/components/ProcessFlow.vue'
import DailyBrief from '@/components/DailyBrief.vue'

defineOptions({ name: 'DailyBriefView' })

const notify = useNotify()
const symInput = ref('')
const symbol = computed(() => symInput.value.trim().toUpperCase())
const watchlistOptions = ref<{ label: string; value: string }[]>([])
const brief = ref<DailyBriefData | null>(null)
const flowSteps = ref<FlowStep[]>([])
const generating = ref(false)

async function loadWatchlist() {
  try {
    const items = await t212Api.watchlist()
    watchlistOptions.value = items.map((w) => ({
      label: `${w.name || w.ticker} (${w.ticker})`,
      value: w.ticker.split('_')[0],
    }))
  } catch { /* 允许手输 */ }
}

async function loadLatest() {
  brief.value = null
  flowSteps.value = []
  if (!symbol.value) return
  const d = await researchApi.briefLatest(symbol.value)
  if (d && (d as DailyBriefData).symbol) brief.value = d as DailyBriefData
}

watch(symbol, () => loadLatest())
onMounted(loadWatchlist)

async function generate() {
  if (!symbol.value || generating.value) return
  generating.value = true
  flowSteps.value = [
    { name: '采集日线价格', status: 'running' },
    { name: '计算技术指标', status: 'pending' },
    { name: '期权 + 趋势计算', status: 'pending' },
    { name: 'LLM 解读', status: 'pending' },
  ]
  try {
    const res = await researchApi.ensureData(symbol.value)
    res.steps.forEach((s, i) => {
      if (i < 2) flowSteps.value[i] = { name: s.name, status: s.status, detail: s.detail }
    })
    if (res.steps.length < 2) flowSteps.value[1] = { name: '计算技术指标', status: 'failed', detail: '前置失败' }
    if (!res.ready) {
      flowSteps.value[2] = { name: '期权 + 趋势计算', status: 'failed', detail: '缺少日线/指标' }
      notify.err(`${symbol.value} 数据补全失败`)
      return
    }
    // ① 数据(图表/数值)快速返回
    flowSteps.value[2] = { name: '期权 + 趋势计算', status: 'running' }
    brief.value = await researchApi.brief(symbol.value)
    flowSteps.value[2] = { name: '期权 + 趋势计算', status: 'done' }
    notify.ok(`${symbol.value} 日报数据已就绪`)
    // ② LLM 解读异步填充(不阻塞图表展示)
    flowSteps.value[3] = { name: 'LLM 解读', status: 'running' }
    const comment = await researchApi.briefComment(symbol.value)
    if (brief.value && comment && comment.core_take) {
      brief.value = { ...brief.value, comment }
      flowSteps.value[3] = { name: 'LLM 解读', status: 'done' }
    } else {
      flowSteps.value[3] = { name: 'LLM 解读', status: 'failed', detail: 'LLM 繁忙，可稍后重试解读' }
    }
  } catch (e) {
    const i = flowSteps.value.findIndex((s) => s.status === 'running')
    if (i >= 0) flowSteps.value[i] = { ...flowSteps.value[i], status: 'failed', detail: apiError(e) }
    notify.err(`生成失败: ${apiError(e)}`)
  } finally {
    generating.value = false
  }
}
</script>

<template>
  <div class="wrap">
    <panel-card title="美股盘前日报 · 单股四维分析（结论 / 趋势 / 期权 / 价位）">
      <div class="ctrl">
        <n-select
          v-model:value="symInput" filterable tag clearable
          placeholder="输入或选择标的，如 AAOI / NIO"
          :options="watchlistOptions" style="max-width:320px"
        />
        <n-button type="primary" :loading="generating" :disabled="!symbol" @click="generate">
          {{ generating ? '生成中…' : '生成盘前日报' }}
        </n-button>
      </div>

      <div v-if="flowSteps.length" class="flow-box">
        <process-flow :steps="flowSteps" title="生成流程" />
      </div>
    </panel-card>

    <div v-if="brief" class="brief-area">
      <daily-brief :data="brief" />
    </div>
    <div v-else-if="symbol && !generating && !flowSteps.length" class="empty faint">
      暂无 {{ symbol }} 的日报，点击「生成盘前日报」。
    </div>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 14px; }
.ctrl { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.flow-box { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel2); }
.brief-area { max-width: 560px; }
.empty { padding: 24px; text-align: center; }
.faint { color: var(--faint); }
</style>
