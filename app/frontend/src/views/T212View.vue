<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NInput, NRadioButton, NRadioGroup, NSpin, NTag, useDialog } from 'naive-ui'
import { useDebounceFn } from '@vueuse/core'
import { dashboardApi, t212Api } from '@/api/endpoints'
import type {
  InstrumentActivity,
  OpenOrder,
  OrderSide,
  SymDetail,
  T212Instrument,
  T212Position,
} from '@/api/types'
import { apiError } from '@/api/client'
import { shortTicker } from '@/composables/format'
import { useNotify } from '@/composables/useNotify'
import { useGroupsStore } from '@/stores/groups'
import PanelCard from '@/components/PanelCard.vue'
import OpenOrdersPanel from '@/components/OpenOrdersPanel.vue'
import ActivityPanel from '@/components/ActivityPanel.vue'
import SymDetailPanel from '@/components/SymDetailPanel.vue'
import OrderModal from '@/components/OrderModal.vue'
import BandModal from '@/components/BandModal.vue'
import AddToGroupModal from '@/components/AddToGroupModal.vue'
import QuantStrategiesPanel from '@/components/QuantStrategiesPanel.vue'

defineOptions({ name: 'T212View' })

const notify = useNotify()
const dialog = useDialog()
const groupsStore = useGroupsStore()

type View = 'positions' | 'watchlist' | 'search'
const view = ref<View>('positions')
const env = ref('demo')

const instruments = ref<T212Instrument[]>([])
const positions = ref<Record<string, T212Position>>({})
const loading = ref(false)

// 搜索
const searchQ = ref('')
const searchLoading = ref(false)

// 自选
const wlAddQ = ref('')
const wlSuggestions = ref<T212Instrument[]>([])
const wlTickers = ref<Set<string>>(new Set()) // 当前自选 ticker 集合，用于搜索视图状态显示

async function loadWlTickers() {
  try {
    const wl = await t212Api.watchlist()
    wlTickers.value = new Set(wl.map((w) => w.ticker))
  } catch {
    // 静默失败，不影响主流程
  }
}

// 挂单
const openOrders = ref<OpenOrder[]>([])
const openOrdersLoading = ref(false)

// 标的详情（点击行展开）
const expandedTicker = ref<string | null>(null)
const symDetail = ref<SymDetail | null>(null)
const symDetailLoading = ref(false)

// 动态
const activityTicker = ref<string | null>(null)
const activity = ref<InstrumentActivity | null>(null)
const activityLoading = ref(false)

// 弹窗
const orderModal = ref(false)
const orderInst = ref<T212Instrument | null>(null)
const orderSide = ref<OrderSide>('buy')
const bandModal = ref(false)
const bandInst = ref<T212Instrument | null>(null)
const groupModal = ref(false)
const groupInst = ref<T212Instrument | null>(null)

function posOf(ticker: string): T212Position | null {
  return positions.value[ticker] ?? null
}

async function loadPositions() {
  loading.value = true
  try {
    positions.value = await t212Api.positions()
    instruments.value = Object.values(positions.value)
  } catch (e) {
    notify.err(`加载持仓失败: ${apiError(e)}`)
  } finally {
    loading.value = false
  }
}

async function loadWatchlist() {
  loading.value = true
  try {
    const wl = await t212Api.watchlist()
    wlTickers.value = new Set(wl.map((w) => w.ticker))
    instruments.value = wl.map((w) => ({
      ...(positions.value[w.ticker] ?? {}),
      ticker: w.ticker,
      name: w.name || w.ticker,
      shortName: w.name || w.ticker,
    }))
  } catch (e) {
    notify.err(`加载自选失败: ${apiError(e)}`)
  } finally {
    loading.value = false
  }
}

async function loadOpenOrders() {
  openOrdersLoading.value = true
  try {
    const d = await t212Api.openOrders()
    openOrders.value = d.items
  } catch {
    openOrders.value = []
  } finally {
    openOrdersLoading.value = false
  }
}

async function openSymDetail(ticker: string) {
  const sym = ticker.split('_')[0].toUpperCase()
  if (expandedTicker.value === ticker) {
    expandedTicker.value = null
    symDetail.value = null
    return
  }
  expandedTicker.value = ticker
  symDetailLoading.value = true
  symDetail.value = null
  const [ind, sent] = await Promise.all([
    dashboardApi.indicators(sym),
    dashboardApi.sentiment(sym, 7),
  ])
  symDetail.value = { ind, sent }
  symDetailLoading.value = false
}

function switchView(v: View) {
  view.value = v
  expandedTicker.value = null
  activityTicker.value = null
  if (v === 'positions') loadPositions()
  else if (v === 'watchlist') loadWatchlist()
  else instruments.value = []
}

async function doSearch() {
  const q = searchQ.value.trim()
  if (!q) return
  searchLoading.value = true
  activityTicker.value = null
  try {
    instruments.value = await t212Api.search(q, 50)
  } catch (e) {
    notify.err(`搜索失败: ${apiError(e)}`)
  } finally {
    searchLoading.value = false
  }
}

const wlSearch = useDebounceFn(async () => {
  const q = wlAddQ.value.trim()
  if (!q) {
    wlSuggestions.value = []
    return
  }
  try {
    wlSuggestions.value = await t212Api.search(q, 8)
  } catch {
    wlSuggestions.value = []
  }
}, 300)

async function wlAdd(inst: T212Instrument) {
  wlSuggestions.value = []
  wlAddQ.value = ''
  try {
    await t212Api.addWatchlist({ ticker: inst.ticker, name: inst.shortName || inst.name || inst.ticker })
    wlTickers.value.add(inst.ticker)
    notify.ok(`${shortTicker(inst.ticker)} 已加入自选`)
    if (view.value === 'watchlist') await loadWatchlist()
  } catch (e) {
    notify.err(`添加失败: ${apiError(e)}`)
  }
}

async function wlAddFromSearch(inst: T212Instrument) {
  try {
    await t212Api.addWatchlist({ ticker: inst.ticker, name: inst.shortName || inst.name || inst.ticker })
    wlTickers.value.add(inst.ticker)
    notify.ok(`${shortTicker(inst.ticker)} 已加入自选`)
  } catch (e) {
    notify.err(`添加失败: ${apiError(e)}`)
  }
}

async function wlRemoveFromSearch(ticker: string) {
  try {
    await t212Api.removeWatchlist(ticker)
    wlTickers.value.delete(ticker)
    notify.ok(`${shortTicker(ticker)} 已移除`)
  } catch (e) {
    notify.err(`移除失败: ${apiError(e)}`)
  }
}

function wlRemove(ticker: string) {
  dialog.warning({
    title: '移除自选',
    content: `从自选移除 ${shortTicker(ticker)}？`,
    positiveText: '移除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await t212Api.removeWatchlist(ticker)
        wlTickers.value.delete(ticker)
        notify.ok(`${shortTicker(ticker)} 已移除`)
        await loadWatchlist()
      } catch (e) {
        notify.err(`移除失败: ${apiError(e)}`)
      }
    },
  })
}

async function toggleActivity(ticker: string) {
  if (activityTicker.value === ticker) {
    activityTicker.value = null
    return
  }
  activityTicker.value = ticker
  activityLoading.value = true
  activity.value = null
  try {
    activity.value = await t212Api.activity(ticker, 7)
  } catch (e) {
    notify.err(`加载动态失败: ${apiError(e)}`)
  } finally {
    activityLoading.value = false
  }
}

function cancelOrder(id: number) {
  dialog.warning({
    title: '取消挂单',
    content: `确认取消挂单 #${id}？`,
    positiveText: '取消挂单',
    negativeText: '返回',
    onPositiveClick: async () => {
      try {
        await t212Api.cancelOrder(id)
        notify.ok(`挂单 #${id} 已取消`)
        await loadOpenOrders()
      } catch (e) {
        notify.err(`取消失败: ${apiError(e)}`)
      }
    },
  })
}

function openOrderModal(inst: T212Instrument, side: OrderSide) {
  orderInst.value = inst
  orderSide.value = side
  orderModal.value = true
}
function openBandModal(inst: T212Instrument) {
  bandInst.value = inst
  bandModal.value = true
}

// 从策略面板点击"管理"：用 t212_ticker 反查持仓/自选列表，构造最简 T212Instrument
function openBandByTicker(symbol: string, t212Ticker: string) {
  // 先从已加载的 instruments 列表找
  const found = instruments.value.find((i) => i.ticker === t212Ticker)
  if (found) {
    openBandModal(found)
    return
  }
  // 列表中没有（当前视图不含该标的），构造最简对象
  bandInst.value = {
    ticker: t212Ticker,
    shortName: symbol,
    name: symbol,
    currentPrice: null,
    currency: null,
    quantity: null,
    ppl: null,
    pnlCurrency: null,
    averagePrice: null,
  } as unknown as T212Instrument
  bandModal.value = true
}
function openGroupModal(inst: T212Instrument) {
  groupInst.value = inst
  groupModal.value = true
}

async function onOrderDone() {
  await Promise.all([loadPositions(), loadOpenOrders()])
}

onMounted(async () => {
  await groupsStore.load()
  await Promise.all([loadPositions(), loadOpenOrders(), loadWlTickers()])
})
</script>

<template>
  <div>
    <!-- 控制栏 -->
    <panel-card>
      <div class="ctrl">
        <n-radio-group :value="view" size="small" @update:value="switchView">
          <n-radio-button value="positions">持仓</n-radio-button>
          <n-radio-button value="watchlist">自选</n-radio-button>
          <n-radio-button value="search">搜索</n-radio-button>
        </n-radio-group>

        <template v-if="view === 'search'">
          <n-input
            v-model:value="searchQ"
            placeholder="输入 Ticker 或公司名称…"
            style="flex: 1; min-width: 200px"
            @keyup.enter="doSearch"
          />
          <n-button type="primary" :loading="searchLoading" :disabled="!searchQ.trim()" @click="doSearch">
            搜索
          </n-button>
        </template>

        <n-button v-if="view === 'positions'" quaternary size="small" @click="loadPositions">⟳ 刷新</n-button>
        <span class="grow" />
        <n-tag size="small" :type="env === 'live' ? 'error' : 'info'" :bordered="false">
          {{ env.toUpperCase() }} 模式
        </n-tag>
      </div>

      <!-- 自选添加 -->
      <div v-if="view === 'watchlist'" class="wl-add">
        <span class="faint nowrap">添加自选:</span>
        <div style="flex: 1; position: relative">
          <n-input
            v-model:value="wlAddQ"
            placeholder="搜索并添加 (如 NVDA)"
            @input="wlSearch"
            @keyup.enter="wlSuggestions[0] && wlAdd(wlSuggestions[0])"
          />
          <div v-if="wlSuggestions.length" class="suggest">
            <div v-for="s in wlSuggestions" :key="s.ticker" class="suggest-item" @click="wlAdd(s)">
              <span class="tag-amber">{{ shortTicker(s.ticker) }}</span>
              <span class="muted">{{ s.shortName || s.name }}</span>
            </div>
          </div>
        </div>
      </div>
    </panel-card>

    <!-- 挂单 -->
    <open-orders-panel
      :orders="openOrders"
      :loading="openOrdersLoading"
      @cancel="cancelOrder"
      @refresh="loadOpenOrders"
    />

    <!-- 循环波段策略概览 -->
    <quant-strategies-panel @manage="openBandByTicker" />

    <!-- 标的列表 -->
    <panel-card v-if="instruments.length">
      <template #header>
        <span class="section-label">{{ view === 'search' ? '搜索结果' : view === 'watchlist' ? '自选标的' : '持仓' }}</span>
        <n-tag size="small" :bordered="false">{{ instruments.length }}</n-tag>
      </template>
      <n-spin :show="loading">
        <table class="inst-table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>名称</th>
              <th v-if="view === 'positions'" class="right">均价</th>
              <th v-if="view === 'positions'" class="right">现价</th>
              <th>持仓</th>
              <th v-if="view === 'positions'" class="right">盈亏</th>
              <th class="right">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="inst in instruments" :key="inst.ticker">
              <tr
                :class="{ 'row-expanded': expandedTicker === inst.ticker }"
                :style="view !== 'search' ? 'cursor:pointer' : ''"
                @click="view !== 'search' && openSymDetail(inst.ticker)"
              >
                <td><span class="tag-amber">{{ shortTicker(inst.ticker) }}</span></td>
                <td class="muted ellip">{{ inst.shortName || inst.name || inst.ticker }}</td>
                <td v-if="view === 'positions'" class="right mono faint">
                  {{ inst.averagePrice != null ? inst.averagePrice.toFixed(4) : (posOf(inst.ticker)?.averagePrice != null ? posOf(inst.ticker)!.averagePrice!.toFixed(4) : '—') }}
                </td>
                <td v-if="view === 'positions'" class="right mono">
                  {{ inst.currentPrice != null ? inst.currentPrice.toFixed(2) + ' ' + (inst.currency || '') : '—' }}
                </td>
                <td class="mono">
                  <n-tag v-if="inst.quantity || posOf(inst.ticker)" size="small" type="success" :bordered="false">
                    {{ inst.quantity ?? posOf(inst.ticker)?.quantity ?? 0 }} 股
                  </n-tag>
                  <span v-else class="faint">—</span>
                </td>
                <td
                  v-if="view === 'positions'"
                  class="right mono"
                  :style="{ color: (inst.ppl ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }"
                >
                  <template v-if="inst.ppl != null">
                    <div>{{ (inst.ppl >= 0 ? '+' : '') + inst.ppl.toFixed(2) + ' ' + (inst.pnlCurrency || '') }}</div>
                    <div
                      v-if="inst.averagePrice && inst.quantity"
                      style="font-size:10px;opacity:0.8"
                    >
                      {{ (inst.ppl >= 0 ? '+' : '') + (inst.ppl / (inst.averagePrice * inst.quantity) * 100).toFixed(2) + '%' }}
                    </div>
                  </template>
                  <span v-else>—</span>
                </td>
                <td class="right" @click.stop>
                  <div class="actions">
                    <n-button size="tiny" type="success" secondary @click="openOrderModal(inst, 'buy')">买入</n-button>
                    <n-button
                      v-if="posOf(inst.ticker)"
                      size="tiny"
                      type="error"
                      secondary
                      @click="openOrderModal(inst, 'sell')"
                    >
                      卖出
                    </n-button>
                    <n-button size="tiny" type="primary" secondary @click="openBandModal(inst)">波段</n-button>
                    <n-button size="tiny" quaternary @click="openGroupModal(inst)">+ 分组</n-button>
                    <n-button
                      size="tiny"
                      :type="activityTicker === inst.ticker ? 'primary' : 'default'"
                      quaternary
                      @click="toggleActivity(inst.ticker)"
                    >
                      动态
                    </n-button>
                    <!-- 搜索结果：加/移自选 -->
                    <template v-if="view === 'search'">
                      <n-button
                        v-if="wlTickers.has(inst.ticker)"
                        size="tiny"
                        type="success"
                        secondary
                        @click="wlRemoveFromSearch(inst.ticker)"
                      >
                        ✓ 自选
                      </n-button>
                      <n-button v-else size="tiny" quaternary @click="wlAddFromSearch(inst)">+ 自选</n-button>
                    </template>
                    <n-button v-if="view === 'watchlist'" size="tiny" type="error" secondary @click="wlRemove(inst.ticker)">
                      移除
                    </n-button>
                  </div>
                </td>
              </tr>
              <tr v-if="expandedTicker === inst.ticker">
                <td :colspan="view === 'positions' ? 7 : 4" style="padding: 4px 0 6px">
                  <sym-detail-panel
                    :symbol="shortTicker(inst.ticker)"
                    :detail="symDetail"
                    :loading="symDetailLoading"
                  />
                </td>
              </tr>
              <tr v-if="activityTicker === inst.ticker">
                <td :colspan="view === 'positions' ? 7 : 4" style="padding: 4px 0 8px">
                  <activity-panel
                    :activity="activity"
                    :loading="activityLoading"
                    @add-to-group="openGroupModal(inst)"
                  />
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </n-spin>
    </panel-card>

    <panel-card v-else>
      <div class="empty">
        <span v-if="view === 'positions'">暂无持仓（demo 账户初始为空）</span>
        <span v-else-if="view === 'watchlist'">自选列表为空，在上方搜索添加标的</span>
        <span v-else>输入关键词搜索 T212 可交易标的</span>
      </div>
    </panel-card>

    <!-- 弹窗 -->
    <order-modal
      v-model:show="orderModal"
      :instrument="orderInst"
      :side="orderSide"
      :position="orderInst ? posOf(orderInst.ticker) : null"
      :env="env"
      @submitted="onOrderDone"
    />
    <band-modal
      v-model:show="bandModal"
      :instrument="bandInst"
      :position="bandInst ? posOf(bandInst.ticker) : null"
      :env="env"
      @submitted="loadOpenOrders"
    />
    <add-to-group-modal v-model:show="groupModal" :instrument="groupInst" @added="groupsStore.load(true)" />
  </div>
</template>

<style scoped>
.ctrl {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.wl-add {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
.suggest {
  position: absolute;
  z-index: 20;
  left: 0;
  right: 0;
  background: var(--panel2);
  border: 1px solid var(--line2);
  border-radius: 5px;
  margin-top: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.suggest-item {
  padding: 8px 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.suggest-item:hover {
  background: rgba(255, 255, 255, 0.05);
}
.inst-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--mono);
  font-size: 12.5px;
}
.inst-table th {
  text-align: left;
  color: var(--faint);
  font-weight: 500;
  font-size: 11px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}
.inst-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--panel2);
  vertical-align: middle;
}
.row-expanded td {
  background: rgba(232, 163, 61, 0.04);
}
.ellip {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actions {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
  flex-wrap: wrap;
}
.empty {
  color: var(--faint);
  text-align: center;
  padding: 32px;
  font-size: 13px;
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
