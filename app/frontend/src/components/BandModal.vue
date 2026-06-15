<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  NButton,
  NInputNumber,
  NModal,
  NSelect,
  NTag,
  NTooltip,
} from 'naive-ui'
import { quantApi, t212Api } from '@/api/endpoints'
import type { QuantStrategyStatus, T212Instrument, T212Position, TimeValidity } from '@/api/types'
import { apiError } from '@/api/client'
import { shortTicker } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'

const props = defineProps<{
  show: boolean
  instrument: T212Instrument | null
  position: T212Position | null
  env: string
}>()
const emit = defineEmits<{
  'update:show': [boolean]
  submitted: []
}>()

const notify = useNotify()

// ── 模式切换 ──────────────────────────────────────────────────────────────────
type Mode = 'loop' | 'once'
const mode = ref<Mode>('loop')

// ── 循环策略 状态 ──────────────────────────────────────────────────────────────
const stratStatus = ref<QuantStrategyStatus | null>(null)
const stratStarting = ref(false)
const stratStopping = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// 策略参数
const buyMode = ref<'ind' | 'market'>('market')
const profitPct = ref(2.0)
const stopLoss = ref(1.0)
const budgetEur = ref(0)
const budgetRatio = ref(50)
const sellRatio = ref(100)
const intervalSec = ref(15)
const maxTradesDay = ref(10)
const rsiBuy = ref(45)

// ── 单次挂单 状态 ──────────────────────────────────────────────────────────────
const buyPrice = ref<number | null>(null)
const sellPrice = ref<number | null>(null)
const buyQty = ref<number | null>(null)
const sellQty = ref<number | null>(null)
const sellPct = ref<number | null>(null)
const buyPct = ref<number | null>(null)
const validity = ref<TimeValidity>('GOOD_TILL_CANCEL')
const onceLoading = ref(false)

const curPrice = computed(
  () => props.instrument?.currentPrice ?? props.position?.currentPrice ?? null,
)

const sym = computed(() => {
  const t = props.instrument?.ticker ?? ''
  return t.split('_')[0].toUpperCase()
})

// ── 轮询策略状态 ──────────────────────────────────────────────────────────────
async function fetchStatus() {
  if (!props.instrument) return
  stratStatus.value = await quantApi.status(sym.value)
}

function startPoll() {
  stopPoll()
  fetchStatus()
  pollTimer = setInterval(fetchStatus, 6000)
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

watch(
  () => props.show,
  (open) => {
    if (open) {
      mode.value = 'loop'
      // 单次挂单重置
      const q = props.instrument?.quantity ?? props.position?.quantity ?? null
      buyPrice.value = null
      sellPrice.value = null
      sellPct.value = null
      buyPct.value = null
      buyQty.value = q
      sellQty.value = q
      validity.value = 'GOOD_TILL_CANCEL'
      // 策略状态轮询
      startPoll()
    } else {
      stopPoll()
    }
  },
)

onUnmounted(stopPoll)

// ── 循环策略 启动 / 停止 ───────────────────────────────────────────────────────
async function startStrategy() {
  if (!props.instrument) return
  stratStarting.value = true
  try {
    await quantApi.start(sym.value, {
      t212_ticker: props.instrument.ticker,
      buy_mode: buyMode.value,
      profit_pct: profitPct.value,
      stop_loss: stopLoss.value,
      budget_ratio: budgetRatio.value,
      budget_eur: budgetEur.value,
      sell_ratio: sellRatio.value,
      interval: intervalSec.value,
      max_trades_day: maxTradesDay.value,
      rsi_buy: rsiBuy.value,
    })
    notify.ok(`${sym.value} 循环波段已启动`)
    await fetchStatus()
  } catch (e) {
    notify.err(`启动失败: ${apiError(e)}`)
  } finally {
    stratStarting.value = false
  }
}

async function stopStrategy() {
  if (!props.instrument) return
  stratStopping.value = true
  try {
    await quantApi.stop(sym.value)
    notify.ok(`${sym.value} 循环波段已停止`)
    await fetchStatus()
  } catch (e) {
    notify.err(`停止失败: ${apiError(e)}`)
  } finally {
    stratStopping.value = false
  }
}

// ── 单次挂单 ──────────────────────────────────────────────────────────────────
function round4(v: number): number {
  return Math.round(v * 10000) / 10000
}
function applySellPct(pct: number | null) {
  sellPct.value = pct
  if (pct != null && curPrice.value != null) sellPrice.value = round4(curPrice.value * (1 + pct / 100))
}
function applyBuyPct(pct: number | null) {
  buyPct.value = pct
  if (pct != null && curPrice.value != null) buyPrice.value = round4(curPrice.value * (1 - pct / 100))
}

async function submitOnce() {
  if (!props.instrument || (!buyPrice.value && !sellPrice.value)) return
  onceLoading.value = true
  try {
    const body: Parameters<typeof t212Api.band>[0] = {
      ticker: props.instrument.ticker,
      timeValidity: validity.value,
    }
    if (sellPrice.value && sellQty.value) {
      body.sellLimitPrice = sellPrice.value
      body.sellQty = sellQty.value
    }
    if (buyPrice.value && buyQty.value) {
      body.buyLimitPrice = buyPrice.value
      body.buyQty = buyQty.value
    }
    const res = (await t212Api.band(body)) as { results: Record<string, unknown> }
    const parts: string[] = []
    if (res.results?.sell_limit) parts.push('卖出限价单')
    if (res.results?.buy_limit) parts.push('买入限价单')
    notify.ok(`波段单已提交: ${parts.join(' + ') || '无'}`)
    if (res.results?.buy_limit_error) notify.err('买入腿失败，请检查后重试')
    emit('submitted')
    emit('update:show', false)
  } catch (e) {
    notify.err(`波段交易失败: ${apiError(e)}`)
  } finally {
    onceLoading.value = false
  }
}

// ── 辅助 ──────────────────────────────────────────────────────────────────────
function fmtTs(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    style="width: 560px"
    :bordered="false"
    @update:show="emit('update:show', $event)"
  >
    <template #header>
      <div class="flex-center gap-8">
        <span>波段交易</span>
        <span class="tag-amber">{{ shortTicker(instrument?.ticker) }}</span>
        <n-tag size="small" :type="env === 'live' ? 'error' : 'info'" :bordered="false">
          {{ (env || 'demo').toUpperCase() }}
        </n-tag>
      </div>
    </template>

    <!-- 模式 Tab -->
    <div class="mode-tabs">
      <button class="mode-btn" :class="{ active: mode === 'loop' }" @click="mode = 'loop'">
        循环策略
      </button>
      <button class="mode-btn" :class="{ active: mode === 'once' }" @click="mode = 'once'">
        单次挂单
      </button>
    </div>

    <!-- ════════════ 循环策略 ════════════ -->
    <template v-if="mode === 'loop'">

      <!-- 运行中状态卡片 -->
      <template v-if="stratStatus?.running">
        <div class="status-card running">
          <div class="status-header">
            <span class="live-dot">●</span>
            <span class="status-title">循环波段运行中</span>
            <span class="faint small">每 {{ stratStatus.params.interval }}s 检查</span>
          </div>

          <div class="status-grid">
            <div class="stat-item">
              <div class="stat-label">持仓状态</div>
              <div>
                <n-tag size="small" :type="stratStatus.holding ? 'success' : 'default'" :bordered="false">
                  {{ stratStatus.holding && stratStatus.quantity != null ? `持有 ${stratStatus.quantity.toFixed(4)} 股` : '空仓' }}
                </n-tag>
              </div>
            </div>
            <div class="stat-item">
              <div class="stat-label">均价 / 现价</div>
              <div class="mono faint">
                {{ stratStatus.avg_price ? stratStatus.avg_price.toFixed(4) : '—' }}
                / {{ stratStatus.last_price ? stratStatus.last_price.toFixed(4) : '—' }}
              </div>
            </div>
            <div class="stat-item">
              <div class="stat-label">浮动盈亏</div>
              <div
                v-if="stratStatus.gain_pct != null"
                :style="{ color: stratStatus.gain_pct >= 0 ? 'var(--up)' : 'var(--down)', fontFamily: 'var(--mono)' }"
              >
                {{ (stratStatus.gain_pct >= 0 ? '+' : '') + stratStatus.gain_pct.toFixed(2) + '%' }}
              </div>
              <div v-else class="faint">—</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">今日 / 总计</div>
              <div class="mono faint">{{ stratStatus.trades_today }} / {{ stratStatus.total_trades }} 笔</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">累计盈亏</div>
              <div
                :style="{ color: (stratStatus.total_pnl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)', fontFamily: 'var(--mono)' }"
              >
                {{ ((stratStatus.total_pnl ?? 0) >= 0 ? '+' : '') + (stratStatus.total_pnl ?? 0).toFixed(2) }}
              </div>
            </div>
            <div class="stat-item">
              <div class="stat-label">上次检查</div>
              <div class="faint small">{{ fmtTs(stratStatus.last_check) }}</div>
            </div>
          </div>

          <div v-if="stratStatus.last_action" class="last-action">
            最近操作: <span class="mono">{{ stratStatus.last_action }}</span>
          </div>
          <div v-if="stratStatus.error" class="error-line">
            错误: {{ stratStatus.error }}
          </div>

          <!-- 指标预热提示 -->
          <div v-if="stratStatus.params.buy_mode === 'ind' && stratStatus.indicators && stratStatus.indicators.ticks < 30" class="hint-warn">
            指标预热中 ({{ stratStatus.indicators.ticks }}/60 ticks)，买入信号暂不触发
          </div>

          <div class="params-summary">
            止盈 {{ stratStatus.params.profit_pct }}% · 止损 {{ stratStatus.params.stop_loss }}%
            · 买入: {{ stratStatus.params.buy_mode === 'market' ? '空仓即市价买' : 'RSI信号' }}
          </div>

          <n-button
            type="error"
            block
            :loading="stratStopping"
            style="margin-top: 12px"
            @click="stopStrategy"
          >
            停止循环
          </n-button>
        </div>
      </template>

      <!-- 已存在但停止中 -->
      <template v-else-if="stratStatus && !stratStatus.running">
        <div class="status-card stopped">
          <div class="status-header">
            <span class="stopped-dot">●</span>
            <span class="status-title">策略已停止</span>
          </div>
          <div class="params-summary faint">
            上次: 止盈 {{ stratStatus.params.profit_pct }}% · 止损 {{ stratStatus.params.stop_loss }}%
            <template v-if="stratStatus.total_trades != null">
              · 总计 {{ stratStatus.total_trades }} 笔
              · 盈亏 {{ (stratStatus.total_pnl ?? 0) >= 0 ? '+' : '' }}{{ (stratStatus.total_pnl ?? 0).toFixed(2) }}
            </template>
          </div>
        </div>
      </template>

      <!-- 配置表单 -->
      <div class="loop-form">
        <div class="form-section-title">{{ stratStatus ? '重新配置并启动' : '策略配置' }}</div>

        <div class="field">
          <label>
            买入触发方式
            <n-tooltip trigger="hover" placement="right">
              <template #trigger><span class="help">?</span></template>
              <div>
                <b>空仓即市价买入</b>：卖出后立即按市价重新建仓，适合趋势震荡行情<br>
                <b>RSI 超卖信号</b>：等待 RSI 低于阈值 + MACD 动能转正才买入，需 5 分钟预热
              </div>
            </n-tooltip>
          </label>
          <n-select
            v-model:value="buyMode"
            :options="[
              { label: '空仓即市价买入（推荐）', value: 'market' },
              { label: 'RSI 超卖信号买入', value: 'ind' },
            ]"
          />
        </div>

        <div v-if="buyMode === 'ind'" class="field">
          <label>RSI 买入阈值（低于此值触发买入）</label>
          <n-input-number v-model:value="rsiBuy" :min="10" :max="70" :step="1">
            <template #suffix>RSI</template>
          </n-input-number>
        </div>

        <div class="two-col">
          <div class="field">
            <label>止盈目标（高于均价 %）</label>
            <n-input-number v-model:value="profitPct" :min="0.1" :max="50" :step="0.5">
              <template #suffix>%</template>
            </n-input-number>
            <div class="quick-btns">
              <button v-for="v in [1, 2, 3, 5]" :key="v" class="qbtn" @click="profitPct = v">{{ v }}%</button>
            </div>
          </div>
          <div class="field">
            <label>止损线（低于均价 %）</label>
            <n-input-number v-model:value="stopLoss" :min="0.1" :max="20" :step="0.5">
              <template #suffix>%</template>
            </n-input-number>
            <div class="quick-btns">
              <button v-for="v in [0.5, 1, 2, 3]" :key="v" class="qbtn" @click="stopLoss = v">{{ v }}%</button>
            </div>
          </div>
        </div>

        <div class="two-col">
          <div class="field">
            <label>
              每次买入金额 €
              <span class="faint small">（0 = 按比例）</span>
            </label>
            <n-input-number v-model:value="budgetEur" :min="0" :step="10" placeholder="0 = 按比例" />
          </div>
          <div class="field">
            <label>买入占可用现金比例</label>
            <n-input-number v-model:value="budgetRatio" :min="1" :max="100" :step="5">
              <template #suffix>%</template>
            </n-input-number>
          </div>
        </div>

        <div class="two-col">
          <div class="field">
            <label>卖出占持仓比例</label>
            <n-input-number v-model:value="sellRatio" :min="1" :max="100" :step="10">
              <template #suffix>%</template>
            </n-input-number>
          </div>
          <div class="field">
            <label>检查间隔</label>
            <n-select
              v-model:value="intervalSec"
              :options="[
                { label: '5 秒', value: 5 },
                { label: '15 秒', value: 15 },
                { label: '30 秒', value: 30 },
                { label: '1 分钟', value: 60 },
                { label: '5 分钟', value: 300 },
              ]"
            />
          </div>
        </div>

        <div class="field">
          <label>每日最大成交笔数（熔断）</label>
          <n-input-number v-model:value="maxTradesDay" :min="1" :max="50" :step="1" />
        </div>

        <div class="hint">
          策略将<b>往复循环</b>直到手动停止：
          持仓时每 {{ intervalSec }}s 检查止盈/止损；
          空仓时{{ buyMode === 'market' ? '立即市价买入' : '等待 RSI 信号买入' }}。
          每日超过 {{ maxTradesDay }} 笔后自动暂停当日交易。
        </div>

        <n-button
          type="primary"
          block
          :loading="stratStarting"
          @click="startStrategy"
        >
          {{ stratStatus?.running ? '重新启动循环' : '启动循环波段' }}
        </n-button>
      </div>
    </template>

    <!-- ════════════ 单次挂单 ════════════ -->
    <template v-else>
      <div class="sub muted">{{ instrument?.shortName || instrument?.name || '' }}</div>

      <div v-if="position" class="pos-box">
        持仓 <n-tag size="small" type="success" :bordered="false">{{ position.quantity }} 股</n-tag>
        &nbsp;均价 <span class="mono muted">{{ position.averagePrice?.toFixed(4) ?? '—' }}</span>
        &nbsp;现价 <span class="mono">{{ curPrice?.toFixed(4) ?? '—' }}</span>
      </div>

      <div class="hint">
        同时挂<span class="down">卖出限价（止盈，高于现价）</span>与<span class="up">买入限价（抄底，低于现价）</span>。至少填一组。
      </div>

      <div class="leg leg-sell">
        <div class="leg-title down">卖出限价（止盈 / 高卖）</div>
        <div class="two-col">
          <div class="field">
            <label>卖出限价</label>
            <n-input-number v-model:value="sellPrice" :min="0.0001" placeholder="高于现价" />
          </div>
          <div class="field">
            <label>卖出股数</label>
            <n-input-number v-model:value="sellQty" :min="0.0001" placeholder="1" />
          </div>
        </div>
        <div class="pct-row">
          <span class="faint">高于现价</span>
          <n-input-number :value="sellPct" size="small" :min="0" :step="0.5" placeholder="%" style="width: 90px" :disabled="curPrice == null" @update:value="applySellPct">
            <template #suffix>%</template>
          </n-input-number>
          <button v-for="p in [3, 5, 10]" :key="p" class="qbtn" :disabled="curPrice == null" @click="applySellPct(p)">+{{ p }}%</button>
        </div>
      </div>

      <div class="leg leg-buy">
        <div class="leg-title up">买入限价（抄底 / 低买）</div>
        <div class="two-col">
          <div class="field">
            <label>买入限价</label>
            <n-input-number v-model:value="buyPrice" :min="0.0001" placeholder="低于现价" />
          </div>
          <div class="field">
            <label>买入股数</label>
            <n-input-number v-model:value="buyQty" :min="0.0001" placeholder="1" />
          </div>
        </div>
        <div class="pct-row">
          <span class="faint">低于现价</span>
          <n-input-number :value="buyPct" size="small" :min="0" :step="0.5" placeholder="%" style="width: 90px" :disabled="curPrice == null" @update:value="applyBuyPct">
            <template #suffix>%</template>
          </n-input-number>
          <button v-for="p in [3, 5, 10]" :key="p" class="qbtn" :disabled="curPrice == null" @click="applyBuyPct(p)">−{{ p }}%</button>
        </div>
        <div v-if="curPrice == null" class="faint small" style="margin-top:6px">现价未知，请直接填绝对限价</div>
      </div>

      <div class="field">
        <label>有效期</label>
        <n-select v-model:value="validity" :options="[
          { label: '撤销前有效 (GTC) — 推荐', value: 'GOOD_TILL_CANCEL' },
          { label: '当日有效 (DAY)', value: 'DAY' },
        ]" />
      </div>

      <n-button type="primary" block :loading="onceLoading" :disabled="!buyPrice && !sellPrice" @click="submitOnce">
        {{ onceLoading ? '提交中…' : '确认挂波段单' }}
      </n-button>
    </template>
  </n-modal>
</template>

<style scoped>
.mode-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 14px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line);
}
.mode-btn {
  flex: 1;
  padding: 7px 0;
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.mode-btn.active {
  background: rgba(232, 163, 61, 0.1);
  color: var(--amber);
}

/* ── 状态卡 ── */
.status-card {
  border-radius: 6px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.status-card.running {
  border: 1px solid rgba(61, 214, 140, 0.3);
  background: rgba(61, 214, 140, 0.04);
}
.status-card.stopped {
  border: 1px solid var(--line);
  background: var(--panel2);
}
.status-header {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 10px;
}
.status-title { font-size: 13px; font-weight: 600; }
.live-dot { color: var(--up); font-size: 10px; animation: pulse 1.5s infinite; }
.stopped-dot { color: var(--faint); font-size: 10px; }
@keyframes pulse { 50% { opacity: 0.3; } }

.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px 12px;
  margin-bottom: 10px;
}
.stat-item {}
.stat-label { font-size: 10px; color: var(--faint); margin-bottom: 2px; }

.last-action {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 6px;
  word-break: break-all;
}
.error-line {
  font-size: 11px;
  color: var(--down);
  margin-bottom: 6px;
}
.hint-warn {
  font-size: 11px;
  color: var(--amber);
  margin-bottom: 6px;
}
.params-summary {
  font-size: 11px;
  color: var(--faint);
}

/* ── 表单 ── */
.loop-form { }
.form-section-title {
  font-size: 11px;
  color: var(--faint);
  letter-spacing: 0.06em;
  margin-bottom: 10px;
}
.field { margin-bottom: 10px; }
.field label {
  display: block;
  font-size: 12px;
  color: var(--faint);
  margin-bottom: 4px;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.quick-btns {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}
.qbtn {
  font-family: var(--mono);
  font-size: 10px;
  padding: 2px 6px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--muted);
  border-radius: 3px;
  cursor: pointer;
}
.qbtn:hover { border-color: var(--amber); color: var(--amber); }
.qbtn:disabled { opacity: 0.35; cursor: default; }

.hint {
  font-size: 11px;
  color: var(--faint);
  padding: 8px 10px;
  background: var(--panel2);
  border-radius: 5px;
  margin-bottom: 12px;
  line-height: 1.6;
}
.help {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--line);
  color: var(--faint);
  font-size: 10px;
  text-align: center;
  line-height: 14px;
  cursor: help;
  vertical-align: middle;
}

/* ── 单次挂单 ── */
.sub { font-size: 12px; margin-bottom: 8px; }
.pos-box {
  margin-bottom: 12px;
  padding: 8px 10px;
  background: var(--panel2);
  border-radius: 5px;
  font-size: 12px;
}
.leg {
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 10px;
}
.leg-sell { border: 1px solid rgba(244, 96, 78, 0.25); }
.leg-buy  { border: 1px solid rgba(61, 214, 140, 0.25); }
.leg-title { font-size: 11px; font-weight: 600; margin-bottom: 8px; letter-spacing: 0.06em; }
.pct-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
}

.tag-amber {
  font-family: var(--mono);
  font-size: 12px;
  background: rgba(232, 163, 61, 0.12);
  color: var(--amber);
  padding: 2px 7px;
  border-radius: 3px;
}
.gap-8 { gap: 8px; }
.flex-center { display: flex; align-items: center; }
.mono { font-family: var(--mono); }
.small { font-size: 11px; }
</style>
