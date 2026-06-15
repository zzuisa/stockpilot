<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NInput, NModal, NSelect } from 'naive-ui'
import { groupsApi } from '@/api/endpoints'
import type { T212Instrument } from '@/api/types'
import { apiError } from '@/api/client'
import { shortTicker } from '@/composables/format'
import { useGroupsStore } from '@/stores/groups'
import { useNotify } from '@/composables/useNotify'

const props = defineProps<{
  show: boolean
  instrument: T212Instrument | null
}>()
const emit = defineEmits<{
  'update:show': [boolean]
  added: []
}>()

const notify = useNotify()
const store = useGroupsStore()

const target = ref<string | null>(null)
const ticker = ref('')

watch(
  () => props.show,
  (open) => {
    if (open) {
      target.value = null
      ticker.value = props.instrument?.ticker ?? ''
      store.load()
    }
  },
)

async function submit() {
  if (!props.instrument || !target.value) return
  const sym = shortTicker(props.instrument.ticker).toUpperCase()
  try {
    await groupsApi.addSymbol(target.value, { symbol: sym, t212_ticker: ticker.value })
    notify.ok(`${sym} 已加入分组 ${target.value}`)
    emit('added')
    emit('update:show', false)
  } catch (e) {
    notify.err(`加入分组失败: ${apiError(e)}`)
  }
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="加入分组"
    style="width: 420px"
    :bordered="false"
    @update:show="emit('update:show', $event)"
  >
    <div class="field">
      <label>选择分组</label>
      <n-select
        v-model:value="target"
        placeholder="-- 请选择 --"
        :options="store.groups.map((g) => ({ label: `${g.name} (${g.id})`, value: g.id }))"
      />
    </div>
    <div class="field">
      <label>T212 Ticker</label>
      <n-input v-model:value="ticker" placeholder="如 NVDA_US_EQ" />
    </div>
    <template #footer>
      <n-button type="primary" block :disabled="!target" @click="submit">确认加入</n-button>
    </template>
  </n-modal>
</template>

<style scoped>
.field {
  margin-bottom: 12px;
}
.field label {
  display: block;
  font-size: 12px;
  color: var(--faint);
  margin-bottom: 4px;
}
</style>
