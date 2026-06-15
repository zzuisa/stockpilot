<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NButton, NTag } from 'naive-ui'
import { quantApi } from '@/api/endpoints'
import type { QuantStrategyStatus } from '@/api/types'
import { useNotify } from '@/composables/useNotify'
import { apiError } from '@/api/client'

const emit = defineEmits<{
  manage: [symbol: string, t212Ticker: string]
}>()

const notify = useNotify()
const strategies = ref<QuantStrategyStatus[]>([])
let pollTimer: ReturnType<typeof setInterval> | null = null
const stoppingMap = ref<Record<string, boolean>>({})

async function load() {
  const list = await quantApi.list()
  // 只展示 active=true 或 running=true 的策略
  strategies.value = list.filter((s) => (s as unknown as { active: boolean }).active || s.running)
}

async function stop(sym: string) {
  stoppingMap.value[sym] = true
  try {
    await quantApi.stop(sym)
    notify.ok(`${sym} 循环已停止`)
    await load()
  } catch (e) {
    notify.err(`停止失败: ${apiError(e)}`)
  } finally {
    stoppingMap.value[sym] = false
  }
}

onMounted(() => {
  load()
  pollTimer = setInterval(load, 8000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div v-if="strategies.length" class="qsp">
    <div class="qsp-header">
      <span class="qsp-title">循环波段策略</span>
      <n-tag size="small" :type="strategies.some(s => s.running) ? 'success' : 'default'" :bordered="false">
        {{ strategies.filter(s => s.running).length }} / {{ strategies.length }} 运行中
      </n-tag>
    </div>

    <table class="qsp-table">
      <thead>
        <tr>
          <th>标的</th>
          <th>状态</th>
          <th class="right">持仓 / 浮盈</th>
          <th class="right">今日 / 累计</th>
          <th class="right">止盈 · 止损</th>
          <th class="right">买入</th>
          <th class="right">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in strategies" :key="s.symbol" class="qsp-row">
          <!-- 标的 -->
          <td>
            <button class="ticker-btn" @click="emit('manage', s.symbol, s.t212_ticker)">
              {{ s.symbol }}
            </button>
          </td>

          <!-- 运行状态 -->
          <td>
            <div class="status-cell">
              <span v-if="s.running" class="live-dot">●</span>
              <span v-else class="stopped-dot">●</span>
              <span class="faint small">{{ s.running ? '运行' : '已停' }}</span>
              <span v-if="s.error" class="err-dot" :title="s.error">!</span>
            </div>
          </td>

          <!-- 持仓 / 浮盈 -->
          <td class="right">
            <template v-if="s.holding && s.quantity != null">
              <span class="mono small">{{ s.quantity.toFixed(s.quantity < 1 ? 4 : 2) }} 股</span>
              <span
                v-if="s.gain_pct != null"
                class="mono small"
                :style="{ color: s.gain_pct >= 0 ? 'var(--up)' : 'var(--down)' }"
              >
                &nbsp;{{ s.gain_pct >= 0 ? '+' : '' }}{{ s.gain_pct.toFixed(2) }}%
              </span>
            </template>
            <span v-else class="faint small">{{ s.running ? '空仓' : '—' }}</span>
          </td>

          <!-- 今日 / 累计 -->
          <td class="right mono small">
            <template v-if="s.running">
              {{ s.trades_today ?? 0 }}
              <span class="faint"> / {{ s.total_trades ?? 0 }}</span>
              <span
                v-if="s.total_pnl != null && s.total_pnl !== 0"
                class="block"
                :style="{ color: s.total_pnl >= 0 ? 'var(--up)' : 'var(--down)' }"
              >
                {{ s.total_pnl >= 0 ? '+' : '' }}{{ s.total_pnl.toFixed(2) }}
              </span>
            </template>
            <span v-else class="faint">—</span>
          </td>

          <!-- 止盈 · 止损 -->
          <td class="right small">
            <span style="color:var(--up)">{{ s.params.profit_pct }}%</span>
            <span class="faint"> · </span>
            <span style="color:var(--down)">{{ s.params.stop_loss }}%</span>
          </td>

          <!-- 买入方式 -->
          <td class="right">
            <n-tag size="tiny" :bordered="false" :type="s.params.buy_mode === 'market' ? 'info' : 'default'">
              {{ s.params.buy_mode === 'market' ? '市价' : 'RSI' }}
            </n-tag>
          </td>

          <!-- 操作 -->
          <td class="right">
            <div class="row-actions">
              <n-button
                size="tiny"
                quaternary
                @click="emit('manage', s.symbol, s.t212_ticker)"
              >
                管理
              </n-button>
              <n-button
                v-if="s.running"
                size="tiny"
                type="error"
                secondary
                :loading="stoppingMap[s.symbol]"
                @click="stop(s.symbol)"
              >
                停止
              </n-button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 最近操作提示（只显示有动作的运行策略） -->
    <div
      v-for="s in strategies.filter(s => s.running && s.last_action)"
      :key="'action-' + s.symbol"
      class="last-action"
    >
      <span class="tag-sym">{{ s.symbol }}</span>
      {{ s.last_action }}
    </div>
  </div>
</template>

<style scoped>
.qsp {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 10px;
}
.qsp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.qsp-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.05em;
}
.qsp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.qsp-table th {
  font-size: 10px;
  color: var(--faint);
  font-weight: 400;
  padding: 2px 6px;
  white-space: nowrap;
}
.qsp-table td {
  padding: 5px 6px;
  border-top: 1px solid var(--line);
  vertical-align: middle;
}
.qsp-row:hover td { background: rgba(232,163,61,0.03); }

.right { text-align: right; }
.block { display: block; }

.ticker-btn {
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 600;
  background: rgba(232,163,61,0.1);
  color: var(--amber);
  border: none;
  border-radius: 3px;
  padding: 2px 7px;
  cursor: pointer;
}
.ticker-btn:hover { background: rgba(232,163,61,0.2); }

.status-cell {
  display: flex;
  align-items: center;
  gap: 4px;
}
.live-dot { color: var(--up); font-size: 8px; animation: pulse 1.5s infinite; }
.stopped-dot { color: var(--faint); font-size: 8px; }
.err-dot {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--down);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  text-align: center;
  line-height: 14px;
  cursor: help;
}
@keyframes pulse { 50% { opacity: 0.3; } }

.row-actions { display: flex; gap: 4px; justify-content: flex-end; }
.mono { font-family: var(--mono); }
.small { font-size: 11px; }

.last-action {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
  margin-top: 6px;
  padding-top: 5px;
  border-top: 1px solid var(--line);
  word-break: break-all;
}
.tag-sym {
  font-family: var(--mono);
  font-size: 10px;
  background: rgba(232,163,61,0.1);
  color: var(--amber);
  padding: 1px 5px;
  border-radius: 3px;
  flex-shrink: 0;
}
</style>
