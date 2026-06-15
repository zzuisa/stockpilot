<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { NTag } from 'naive-ui'
import { streamApi } from '@/api/endpoints'
import type { RTQuote, RTTrade, SymDetail } from '@/api/types'
import { sentLabel, sentType } from '@/composables/format'
import TradeChart from '@/components/TradeChart.vue'

const props = defineProps<{
  symbol: string
  detail: SymDetail | null
  loading: boolean
}>()

// ── 实时行情 ──────────────────────────────────────────────────────────────────
const quote = ref<RTQuote | null>(null)
const trades = ref<RTTrade[]>([])
const quoteLoading = ref(false)
let es: EventSource | null = null

async function startRt() {
  stopRt()
  quoteLoading.value = true
  quote.value = await streamApi.quote(props.symbol)
  quoteLoading.value = false

  es = new EventSource(streamApi.tradesUrl(props.symbol))
  es.onmessage = (e) => {
    try {
      const t: RTTrade = JSON.parse(e.data)
      if (t.p) {
        trades.value.unshift(t)
        if (trades.value.length > 500) trades.value.length = 500
        if (quote.value) quote.value.last = t.p
      }
    } catch { /* ignore */ }
  }
}

function stopRt() {
  es?.close()
  es = null
  trades.value = []
  quote.value = null
}

watch(() => props.symbol, () => { startRt() })
onMounted(() => { startRt() })
onUnmounted(() => { stopRt() })
</script>

<template>
  <div class="sym-drawer">
    <div v-if="loading" class="faint small">加载中…</div>
    <div v-else-if="!detail" class="faint small">无法加载详情</div>
    <template v-else>

      <!-- 指标 + 情绪 双栏 -->
      <div class="cols">
        <!-- 技术指标 -->
        <div>
          <div class="col-title">技术指标</div>
          <template v-if="detail.ind">
            <div class="ind-row">
              <span>RSI(14)</span>
              <span :style="{
                color: detail.ind.rsi == null ? 'var(--faint)'
                     : detail.ind.rsi < 30 ? 'var(--up)'
                     : detail.ind.rsi > 70 ? 'var(--down)'
                     : 'var(--muted)'
              }">
                {{ detail.ind.rsi != null ? detail.ind.rsi.toFixed(1) : '—' }}
              </span>
            </div>
            <div class="ind-row">
              <span>MACD</span>
              <n-tag size="tiny"
                :type="detail.ind.macd_cross === 1 ? 'success' : detail.ind.macd_cross === -1 ? 'error' : 'default'"
                :bordered="false">
                {{ detail.ind.macd_cross === 1 ? '金叉' : detail.ind.macd_cross === -1 ? '死叉' : '中性' }}
              </n-tag>
            </div>
            <div class="ind-row"><span>SMA20</span><span class="muted">{{ detail.ind.sma20?.toFixed(2) ?? '—' }}</span></div>
            <div class="ind-row"><span>SMA50</span><span class="muted">{{ detail.ind.sma50?.toFixed(2) ?? '—' }}</span></div>
            <div class="ind-row"><span>SMA200</span><span class="muted">{{ detail.ind.sma200?.toFixed(2) ?? '—' }}</span></div>
            <div class="ind-row"><span>ATR</span><span class="muted">{{ detail.ind.atr?.toFixed(2) ?? '—' }}</span></div>
            <div class="ind-row"><span>量比</span><span class="muted">{{ detail.ind.vol_ratio?.toFixed(2) ?? '—' }}</span></div>
          </template>
          <div v-else class="faint small">无数据，先运行 signals 任务</div>
        </div>

        <!-- 情绪分析 -->
        <div>
          <div class="col-title">情绪分析 (近7天)</div>
          <template v-if="detail.sent">
            <div class="ind-row">
              <span>新闻情绪</span>
              <n-tag size="tiny" :type="sentType(Math.round(detail.sent.aggregates.sent_avg ?? 0))" :bordered="false">
                {{ detail.sent.aggregates.sent_avg != null
                   ? (detail.sent.aggregates.sent_avg >= 0 ? '+' : '') + detail.sent.aggregates.sent_avg.toFixed(2)
                   : '—' }}
              </n-tag>
            </div>
            <div class="ind-row">
              <span>社区情绪</span>
              <n-tag size="tiny" :type="sentType(Math.round(detail.sent.aggregates.comm_avg ?? 0))" :bordered="false">
                {{ detail.sent.aggregates.comm_avg != null
                   ? (detail.sent.aggregates.comm_avg >= 0 ? '+' : '') + detail.sent.aggregates.comm_avg.toFixed(2)
                   : '—' }}
              </n-tag>
            </div>
            <div class="ind-row">
              <span>社区信号</span>
              <n-tag size="tiny" :bordered="false">{{ detail.sent.label || '—' }}</n-tag>
            </div>
            <div class="ind-row"><span>新闻条数</span><span class="muted">{{ detail.sent.aggregates.news_cnt || 0 }}</span></div>
            <div class="ind-row"><span>社区帖数</span><span class="muted">{{ detail.sent.aggregates.comm_cnt || 0 }}</span></div>
            <div class="ind-row"><span>社区正向</span><span style="color:var(--up)">{{ detail.sent.aggregates.comm_pos_cnt || 0 }}</span></div>
            <div class="ind-row"><span>社区负向</span><span style="color:var(--down)">{{ detail.sent.aggregates.comm_neg_cnt || 0 }}</span></div>
            <template v-if="detail.sent.news.length">
              <div class="col-title" style="margin-top:10px;border-top:1px solid var(--line);padding-top:8px">最近新闻</div>
              <div v-for="(n, i) in detail.sent.news.slice(0, 3)" :key="i" class="news-item">
                <n-tag size="tiny" :type="sentType(n.score)" :bordered="false">{{ sentLabel(n.score) }}</n-tag>
                <a :href="n.url" target="_blank" rel="noopener" class="news-link">{{ n.title }}</a>
              </div>
            </template>
          </template>
          <div v-else class="faint small">无数据，先运行 sentiment 任务</div>
        </div>
      </div>

      <!-- 实时行情 -->
      <div class="rt-section">
        <!-- 报价头 -->
        <div class="rt-header">
          <span class="col-title" style="margin:0">实时行情</span>
          <span v-if="quote?.source === 'finnhub'" class="rt-live">● 实时</span>
          <span v-else-if="quote?.source === 't212_position'" class="rt-live">● T212</span>
          <span v-else-if="quote?.source === 'yfinance'" class="faint small">~15min 延迟</span>
          <span v-if="quoteLoading" class="faint small">加载中…</span>
          <template v-else-if="quote?.last != null">
            <span class="rt-price">{{ quote.last.toFixed(2) }}</span>
            <span v-if="quote.change_pct != null"
              :style="{ color: quote.change_pct >= 0 ? 'var(--up)' : 'var(--down)', fontSize: '12px' }">
              {{ (quote.change_pct >= 0 ? '+' : '') + quote.change_pct.toFixed(2) + '%' }}
            </span>
            <span v-if="quote.high != null && quote.low != null" class="faint small">
              H {{ quote.high.toFixed(2) }} / L {{ quote.low.toFixed(2) }}
            </span>
          </template>
          <span class="faint small" style="margin-left:auto">{{ trades.length }} 笔</span>
        </div>

        <!-- Canvas 成交图 -->
        <trade-chart :trades="trades" :current-price="quote?.last ?? null" />
      </div>

    </template>
  </div>
</template>

<style scoped>
.sym-drawer {
  background: var(--panel2);
  border-radius: 6px;
  padding: 12px 14px;
  margin: 2px 0 4px;
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 14px;
}
@media (max-width: 680px) { .cols { grid-template-columns: 1fr; } }
.col-title {
  font-size: 11px;
  color: var(--faint);
  margin-bottom: 8px;
  letter-spacing: .06em;
}
.ind-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
  font-size: 12px;
}
.ind-row:last-child { border-bottom: none; }
.ind-row > span:first-child { color: var(--faint); }
.news-item {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-bottom: 5px;
  font-size: 11px;
}
.news-link { color: var(--text); text-decoration: none; line-height: 1.4; }
.news-link:hover { color: var(--amber); }

/* 实时区 */
.rt-section { border-top: 1px solid var(--line); padding-top: 10px; }
.rt-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.rt-live { font-size: 10px; color: var(--up); animation: rtpulse 1.5s infinite; }
@keyframes rtpulse { 50% { opacity: .3; } }
.rt-price { font-family: var(--mono); font-size: 16px; font-weight: 600; }
.small { font-size: 11px; }
</style>
