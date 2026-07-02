<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { NBadge, NPopover, NTag } from 'naive-ui'
import { dashboardApi } from '@/api/endpoints'
import type { DataUpdate } from '@/api/types'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()
const updates = ref<DataUpdate[]>([])
const LAST_SEEN_KEY = 'sp_updates_last_seen'
const lastSeen = ref<string>(localStorage.getItem(LAST_SEEN_KEY) || '')
let timer: ReturnType<typeof setInterval> | undefined

const unreadCount = computed(
  () => updates.value.filter((u) => !lastSeen.value || u.ts > lastSeen.value).length,
)

const KIND_META: Record<string, { icon: string; label: string; type: 'info' | 'success' | 'warning' | 'error' | 'default' }> = {
  news: { icon: '📰', label: '新闻', type: 'info' },
  sentiment: { icon: '🧠', label: '情绪', type: 'success' },
  signal: { icon: '⚡', label: '信号', type: 'warning' },
  trade: { icon: '💱', label: '成交', type: 'default' },
  data: { icon: '📊', label: '数据', type: 'info' },
}
function meta(kind: string) {
  return KIND_META[kind] ?? { icon: '•', label: kind, type: 'default' as const }
}

function fmtTime(ts: string): string {
  return ts.slice(5, 16).replace('T', ' ')
}

async function poll(initial = false) {
  const latest = updates.value[0]?.ts
  const fresh = await dashboardApi.updates(initial ? undefined : latest, 50)
  if (!fresh.length) return
  // 合并去重(按 id)，最新在前
  const seen = new Set(updates.value.map((u) => u.id))
  const add = fresh.filter((u) => !seen.has(u.id))
  if (!add.length) return
  updates.value = [...add, ...updates.value].slice(0, 80)
  if (!initial) {
    // 新更新弹提示，简明汇总哪些股票更新了什么
    const head = add.slice(0, 3).map((u) => `${meta(u.kind).icon}${u.title}`).join('；')
    notify.info(add.length > 3 ? `${head} 等 ${add.length} 条更新` : head)
  }
}

function markSeen() {
  if (updates.value.length) {
    lastSeen.value = updates.value[0].ts
    localStorage.setItem(LAST_SEEN_KEY, lastSeen.value)
  }
}

onMounted(() => {
  poll(true)
  timer = setInterval(poll, 30000)   // 30s 轮询，实时感知更新
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<template>
  <n-popover trigger="click" placement="bottom-end" :show-arrow="false" @update:show="(v: boolean) => v && markSeen()">
    <template #trigger>
      <button class="bell" title="更新动态">
        <n-badge :value="unreadCount" :max="99" :show="unreadCount > 0">
          <span class="bell-icon">🔔</span>
        </n-badge>
      </button>
    </template>
    <div class="pop">
      <div class="pop-title">更新动态</div>
      <div v-if="!updates.length" class="faint small empty">暂无更新</div>
      <div v-else class="list">
        <div v-for="u in updates.slice(0, 40)" :key="u.id" class="row"
             :class="{ unread: !lastSeen || u.ts > lastSeen }">
          <n-tag size="tiny" :type="meta(u.kind).type" :bordered="false">
            {{ meta(u.kind).icon }} {{ meta(u.kind).label }}
          </n-tag>
          <span class="title">{{ u.title }}</span>
          <span class="time mono faint">{{ fmtTime(u.ts) }}</span>
        </div>
      </div>
    </div>
  </n-popover>
</template>

<style scoped>
.bell { background: none; border: none; cursor: pointer; padding: 2px 4px; display: flex; align-items: center; }
.bell-icon { font-size: 16px; line-height: 1; }
.pop { min-width: 320px; max-width: 380px; padding: 2px 0; }
.pop-title { font-size: 11px; color: var(--faint); padding: 0 4px 8px; letter-spacing: 0.05em; }
.empty { padding: 12px 4px; }
.list { max-height: 420px; overflow-y: auto; }
.row { display: flex; align-items: center; gap: 8px; padding: 7px 6px; border-radius: 4px; font-size: 12px; border-left: 2px solid transparent; }
.row.unread { background: rgba(232, 163, 61, 0.06); border-left-color: var(--amber); }
.row:hover { background: rgba(232, 163, 61, 0.1); }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.time { font-size: 10px; flex-shrink: 0; }
</style>
