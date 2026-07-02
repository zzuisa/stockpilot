<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInputNumber, NModal, NRadioButton, NRadioGroup, NSelect, NTag } from 'naive-ui'
import { useDebounceFn } from '@vueuse/core'
import { t212Api } from '@/api/endpoints'
import type { OrderKind, OrderSide, T212Instrument, T212Position, TimeValidity } from '@/api/types'
import { apiError } from '@/api/client'
import { shortTicker } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'

const props = defineProps<{
  show: boolean
  instrument: T212Instrument | null
  side: OrderSide
  position: T212Position | null
  env: string
}>()
const emit = defineEmits<{
  'update:show': [boolean]
  submitted: []
}>()

const notify = useNotify()

const side = ref<OrderSide>('buy')
const kind = ref<OrderKind>('market')
const qty = ref<number | null>(null)
const value = ref<number | null>(null)
const currency = ref<'USD' | 'EUR'>('USD')   // 金额币种,默认美元(标的币种)
const limitPrice = ref<number | null>(null)
const stopPrice = ref<number | null>(null)
const validity = ref<TimeValidity>('DAY')
const loading = ref(false)

// 实时现价（每次后端拉取，仅用于按金额→股数换算，绝不用前端静态值）
const livePrice = ref<number | null>(null)
const quoting = ref(false)

const ticker = computed(() => props.instrument?.ticker ?? '')

/** 拉取实时现价并返回；失败返回 0 */
async function refreshQuote(): Promise<number> {
  if (!ticker.value) return 0
  quoting.value = true
  try {
    const { price } = await t212Api.quote(ticker.value)
    livePrice.value = price
    return price
  } catch {
    livePrice.value = null
    return 0
  } finally {
    quoting.value = false
  }
}

// 金额变化时（市价买入）后台实时取价并预估股数
const debouncedQuote = useDebounceFn(refreshQuote, 500)
watch(
  () => [value.value, kind.value, side.value],
  () => {
    if (kind.value === 'market' && side.value === 'buy' && value.value) debouncedQuote()
  },
)

// 按"实时现价"换算出的预估股数（仅 USD 金额精确展示；EUR 由后端换算）
const estShares = computed(() => {
  if (currency.value !== 'USD') return null
  if (!value.value || !livePrice.value || livePrice.value <= 0) return null
  return Math.round((value.value / livePrice.value) * 1e4) / 1e4
})

// 打开时同步外部传入的方向并重置表单
watch(
  () => props.show,
  (open) => {
    if (open) {
      side.value = props.side
      kind.value = 'market'
      qty.value = null
      value.value = null
      currency.value = 'USD'
      limitPrice.value = null
      stopPrice.value = null
      validity.value = 'DAY'
      livePrice.value = null
    }
  },
)

const submitDisabled = computed(() => {
  if (kind.value === 'market') return !qty.value && !value.value
  if (kind.value === 'limit') return !qty.value || !limitPrice.value
  return !qty.value || !stopPrice.value
})

async function submit() {
  if (!props.instrument || submitDisabled.value) return
  loading.value = true
  try {
    if (kind.value === 'limit') {
      await t212Api.limitOrder({
        ticker: ticker.value,
        side: side.value,
        quantity: qty.value!,
        limitPrice: limitPrice.value!,
        timeValidity: validity.value,
      })
    } else if (kind.value === 'stop') {
      await t212Api.stopOrder({
        ticker: ticker.value,
        side: side.value,
        quantity: qty.value!,
        stopPrice: stopPrice.value!,
        timeValidity: validity.value,
      })
    } else {
      // 市价单：按金额买入时交给后端按所选币种换算 + 余额预校验(只发 quantity)。
      if (side.value === 'buy' && !qty.value && value.value) {
        await t212Api.marketOrder({
          ticker: ticker.value,
          side: 'buy',
          value: value.value,
          currency: currency.value,
        })
      } else {
        if (!qty.value || qty.value <= 0) {
          notify.err('请填写有效股数或金额')
          return
        }
        await t212Api.marketOrder({
          ticker: ticker.value,
          side: side.value,
          quantity: qty.value,
        })
      }
    }
    const kindLabel = kind.value === 'limit' ? '限价' : kind.value === 'stop' ? '止损' : '市价'
    notify.ok(`${side.value === 'buy' ? '买入' : '卖出'}${kindLabel}订单已提交`)
    emit('submitted')
    emit('update:show', false)
  } catch (e) {
    notify.err(`下单失败: ${apiError(e)}`)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    style="width: 460px"
    :bordered="false"
    @update:show="emit('update:show', $event)"
  >
    <template #header>
      <div class="flex-center gap-8">
        <span>{{ side === 'buy' ? '买入' : '卖出' }}</span>
        <span class="tag-amber">{{ shortTicker(ticker) }}</span>
        <n-tag size="small" :type="env === 'live' ? 'error' : 'info'" :bordered="false">
          {{ (env || 'demo').toUpperCase() }}
        </n-tag>
      </div>
    </template>

    <div class="sub muted">{{ instrument?.shortName || instrument?.name || '' }}</div>

    <div v-if="position" class="pos-box">
      持仓 <n-tag size="small" type="success" :bordered="false">{{ position.quantity }} 股</n-tag>
      &nbsp;均价 <span class="mono muted">{{ position.averagePrice?.toFixed(2) ?? '—' }}</span>
      &nbsp;现价 <span class="mono">{{ position.currentPrice?.toFixed(2) ?? '—' }}</span>
    </div>

    <div class="block">
      <n-radio-group v-model:value="side" size="small">
        <n-radio-button value="buy">买入</n-radio-button>
        <n-radio-button value="sell">卖出</n-radio-button>
      </n-radio-group>
    </div>

    <div class="block">
      <div class="lbl">订单类型</div>
      <n-radio-group v-model:value="kind" size="small">
        <n-radio-button value="market">市价单</n-radio-button>
        <n-radio-button value="limit">限价单</n-radio-button>
        <n-radio-button value="stop">止损单</n-radio-button>
      </n-radio-group>
    </div>

    <!-- 市价买入 -->
    <div v-if="kind === 'market' && side === 'buy'">
      <div class="field" style="margin-bottom: 8px">
        <label>金额币种（默认美元；选欧元由后端按 T212 汇率折算）</label>
        <n-radio-group v-model:value="currency" size="small">
          <n-radio-button value="USD">美元 USD</n-radio-button>
          <n-radio-button value="EUR">欧元 EUR</n-radio-button>
        </n-radio-group>
      </div>
      <div class="two-col">
        <div class="field">
          <label>金额 ({{ currency === 'USD' ? '$' : '€' }})</label>
          <n-input-number v-model:value="value" :min="1" placeholder="150" @update:value="qty = null" />
        </div>
        <div class="field">
          <label>股数</label>
          <n-input-number v-model:value="qty" :min="0.0001" placeholder="1" @update:value="value = null" />
        </div>
      </div>
    </div>
    <!-- 金额→股数 实时预估（现价由后端实时拉取） -->
    <div v-if="kind === 'market' && side === 'buy' && value && !qty" class="conv-hint">
      <template v-if="currency === 'USD'">
        <span v-if="quoting" class="faint">实时取价中…</span>
        <template v-else-if="livePrice">
          实时现价 <span class="mono">{{ livePrice.toFixed(4) }} USD</span>
          · 预估 ≈ <span class="mono amber">{{ estShares }}</span> 股
        </template>
        <span v-else class="faint">现价待获取，提交时由后端换算</span>
        <n-button text size="tiny" :loading="quoting" style="margin-left: 6px" @click="refreshQuote">⟳</n-button>
      </template>
      <span v-else class="faint">欧元金额将由后端按 T212 反推汇率换算成股数（提交前会校验余额）</span>
    </div>
    <!-- 市价卖出 -->
    <div v-if="kind === 'market' && side === 'sell'" class="field">
      <label>卖出股数</label>
      <n-input-number v-model:value="qty" :min="0.0001" :placeholder="String(position?.quantity ?? 1)" />
    </div>
    <!-- 限价 -->
    <div v-if="kind === 'limit'" class="two-col">
      <div class="field">
        <label>限价（触发价格）</label>
        <n-input-number v-model:value="limitPrice" :min="0.0001" placeholder="0.00" />
      </div>
      <div class="field">
        <label>股数</label>
        <n-input-number v-model:value="qty" :min="0.0001" :placeholder="String(position?.quantity ?? 1)" />
      </div>
    </div>
    <!-- 止损 -->
    <div v-if="kind === 'stop'" class="two-col">
      <div class="field">
        <label>触发价（止损/止盈）</label>
        <n-input-number v-model:value="stopPrice" :min="0.0001" placeholder="0.00" />
      </div>
      <div class="field">
        <label>股数</label>
        <n-input-number v-model:value="qty" :min="0.0001" :placeholder="String(position?.quantity ?? 1)" />
      </div>
    </div>

    <div v-if="kind !== 'market'" class="field">
      <label>有效期</label>
      <n-select
        v-model:value="validity"
        :options="[
          { label: '当日有效 (DAY)', value: 'DAY' },
          { label: '撤销前有效 (GTC)', value: 'GOOD_TILL_CANCEL' },
        ]"
      />
    </div>

    <div v-if="kind !== 'market'" class="hint">免费 API 限速 1 req/2s</div>

    <template #footer>
      <n-button type="primary" block :loading="loading" :disabled="submitDisabled" @click="submit">
        确认{{ side === 'buy' ? '买入' : '卖出' }}{{
          kind === 'limit' ? '（限价）' : kind === 'stop' ? '（止损）' : ''
        }}
      </n-button>
    </template>
  </n-modal>
</template>

<style scoped>
.sub {
  font-size: 12px;
  margin-bottom: 12px;
}
.pos-box {
  margin-bottom: 12px;
  padding: 8px 10px;
  background: var(--panel2);
  border-radius: 5px;
  font-size: 12px;
}
.block {
  margin-bottom: 12px;
}
.lbl {
  font-size: 11px;
  color: var(--faint);
  margin-bottom: 5px;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 10px;
}
.field {
  margin-bottom: 10px;
}
.field label {
  display: block;
  font-size: 12px;
  color: var(--faint);
  margin-bottom: 4px;
}
.hint {
  font-size: 11px;
  color: var(--faint);
  padding: 8px 10px;
  background: var(--panel2);
  border-radius: 5px;
  margin-bottom: 4px;
}
.conv-hint {
  font-size: 12px;
  color: var(--muted);
  margin: -2px 0 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}
.tag-amber {
  font-family: var(--mono);
  font-size: 12px;
  background: rgba(232, 163, 61, 0.12);
  color: var(--amber);
  padding: 2px 7px;
  border-radius: 3px;
}
</style>
