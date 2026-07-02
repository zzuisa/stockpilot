<script setup lang="ts">
/**
 * 通用流程可视化组件：展示多步骤任务的实时状态。
 * 可用于研究分析、数据补全、任意需要"当前进行到哪一步"的可视化提醒场景。
 */
import { NSpin } from 'naive-ui'

export type FlowStatus = 'pending' | 'running' | 'done' | 'failed'
export interface FlowStep {
  name: string
  status: FlowStatus
  detail?: string | null
}

defineProps<{
  steps: FlowStep[]
  title?: string
}>()

const ICON: Record<FlowStatus, string> = {
  pending: '○', running: '', done: '✓', failed: '✗',
}
</script>

<template>
  <div class="flow">
    <div v-if="title" class="flow-title">{{ title }}</div>
    <div v-for="(s, i) in steps" :key="i" class="step" :class="s.status">
      <div class="rail">
        <span class="node">
          <n-spin v-if="s.status === 'running'" :size="12" />
          <span v-else class="ic">{{ ICON[s.status] }}</span>
        </span>
        <span v-if="i < steps.length - 1" class="line" :class="{ on: s.status === 'done' }" />
      </div>
      <div class="body">
        <div class="name">{{ s.name }}</div>
        <div v-if="s.detail" class="detail">{{ s.detail }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.flow { display: flex; flex-direction: column; }
.flow-title { font-size: 11px; color: var(--faint); letter-spacing: 0.05em; margin-bottom: 8px; }
.step { display: flex; gap: 10px; }
.rail { display: flex; flex-direction: column; align-items: center; }
.node {
  width: 22px; height: 22px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; font-size: 12px; font-weight: 700;
  border: 1.5px solid var(--line2); color: var(--faint);
  background: var(--panel2);
}
.line { width: 2px; flex: 1; min-height: 14px; background: var(--line); margin: 2px 0; }
.line.on { background: var(--up); }
.body { padding-bottom: 14px; }
.name { font-size: 13px; color: var(--text); line-height: 22px; }
.detail { font-size: 11px; color: var(--muted); margin-top: -2px; }

.step.done .node { border-color: var(--up); color: var(--up); }
.step.done .name { color: var(--text); }
.step.running .node { border-color: var(--amber); }
.step.running .name { color: var(--amber); }
.step.failed .node { border-color: var(--down); color: var(--down); }
.step.failed .name { color: var(--down); }
.step.pending .name { color: var(--faint); }
</style>
