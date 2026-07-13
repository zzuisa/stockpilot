<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NInputNumber, NSwitch } from 'naive-ui'
import { settingsApi } from '@/api/endpoints'
import { useNotify } from '@/composables/useNotify'

// 全 Agent 托管设置。开启后托管循环会自行分析→反思→决策，并在预算内自动下单；
// 超单笔预算的订单升级为人工确认。kill-switch 一键停一切自动执行。
const notify = useNotify()
const loading = ref(true)
const saving = ref(false)

const autonomyEnabled = ref(false)
const killSwitch = ref(false)
const mcpExposeWrite = ref(false)
const maxOrderEur = ref<number>(200)
const autoExecMaxEur = ref<number>(50)
const dailyAutoTrades = ref<number>(3)

async function load() {
  loading.value = true
  try {
    const data = await settingsApi.get()
    const s = data.settings || {}
    autonomyEnabled.value = !!s.autonomy_enabled
    killSwitch.value = !!s.kill_switch
    mcpExposeWrite.value = !!s.mcp_expose_write
    const b = s.risk_budget || {}
    maxOrderEur.value = Number(b.max_order_eur ?? 200)
    autoExecMaxEur.value = Number(b.auto_execute_max_eur ?? 50)
    dailyAutoTrades.value = Number(b.daily_auto_trades ?? 3)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await settingsApi.put({
      autonomy_enabled: autonomyEnabled.value,
      kill_switch: killSwitch.value,
      mcp_expose_write: mcpExposeWrite.value,
      risk_budget: {
        max_order_eur: maxOrderEur.value,
        auto_execute_max_eur: autoExecMaxEur.value,
        daily_auto_trades: dailyAutoTrades.value,
      },
    })
    notify.success('设置已保存')
  } catch {
    notify.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="settings-view">
    <h2 class="title">设置 / 全 Agent 托管</h2>
    <p class="muted small">
      开启托管后，Agent 会自行分析→反思→决策，并在下方「单笔自动执行上限」内自动下单；超过上限的订单会升级为人工确认（网页 / Telegram）。
    </p>

    <div v-if="!loading" class="cards">
      <div class="card danger" :class="{ active: killSwitch }">
        <div class="row">
          <div>
            <div class="k">🛑 Kill-switch（紧急停）</div>
            <div class="muted small">立即禁止一切自动执行（改策略 / 自动下单）。托管开关不受影响，但被此项压制。</div>
          </div>
          <n-switch v-model:value="killSwitch" />
        </div>
      </div>

      <div class="card" :class="{ active: autonomyEnabled }">
        <div class="row">
          <div>
            <div class="k">🤖 全 Agent 托管</div>
            <div class="muted small">定时让 supervisor 自主分析并调整量化策略 / 建单。</div>
          </div>
          <n-switch v-model:value="autonomyEnabled" :disabled="killSwitch" />
        </div>
      </div>

      <div class="card">
        <div class="k">💶 风险预算</div>
        <div class="grid">
          <label>单笔金额上限 (€)<n-input-number v-model:value="maxOrderEur" :min="1" /></label>
          <label>单笔自动执行上限 (€)<n-input-number v-model:value="autoExecMaxEur" :min="0" /></label>
          <label>每日自动成交笔数<n-input-number v-model:value="dailyAutoTrades" :min="0" /></label>
        </div>
        <div class="muted small">托管仅在「单笔自动执行上限」内自动成交；超过则建 pending 单等你确认。</div>
      </div>

      <div class="card">
        <div class="row">
          <div>
            <div class="k">🔌 MCP 暴露写能力</div>
            <div class="muted small">是否允许外部 Agent 经 MCP 调用下单 / 改策略工具（默认关，仅暴露只读）。</div>
          </div>
          <n-switch v-model:value="mcpExposeWrite" />
        </div>
      </div>

      <n-button type="primary" :loading="saving" @click="save">保存设置</n-button>
    </div>
    <div v-else class="muted">加载中…</div>
  </div>
</template>

<style scoped>
.settings-view { max-width: 720px; margin: 0 auto; padding: 8px 4px; }
.title { font-size: 16px; color: var(--text); margin: 4px 0 8px; }
.muted { color: var(--muted); } .small { font-size: 12px; }
.cards { display: flex; flex-direction: column; gap: 12px; margin-top: 14px; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--panel2); }
.card.active { border-color: var(--amber); }
.card.danger.active { border-color: var(--down); }
.row { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.k { font-size: 13.5px; color: var(--text); font-weight: 600; margin-bottom: 3px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 10px 0; }
.grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
</style>
