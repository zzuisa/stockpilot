<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NDataTable, NSelect, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { notifyApi } from '@/api/endpoints'
import type { NotifyRoute } from '@/api/types'
import { apiError } from '@/api/client'
import { useGroupsStore } from '@/stores/groups'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'

defineOptions({ name: 'RoutesView' })

const notify = useNotify()
const groupsStore = useGroupsStore()
const routes = ref<NotifyRoute[]>([])
const filter = ref<string>('')

async function load() {
  try {
    routes.value = await notifyApi.routes(filter.value || undefined)
  } catch (e) {
    notify.err(`加载路由失败: ${apiError(e)}`)
  }
}

onMounted(async () => {
  await groupsStore.load()
  await load()
})

const groupOptions = () => [
  { label: '全部分组', value: '' },
  ...groupsStore.groups.map((g) => ({ label: `${g.name} (${g.id})`, value: g.id })),
]

const columns: DataTableColumns<NotifyRoute> = [
  {
    title: '分组',
    key: 'group_id',
    render: (r) => h(NTag, { size: 'small', bordered: false }, { default: () => r.group_id }),
  },
  { title: '标的', key: 'symbol', render: (r) => r.symbol ?? '(组级)' },
  {
    title: '渠道',
    key: 'channel',
    render: (r) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: r.channel === 'telegram' ? 'success' : 'warning' },
        { default: () => r.channel },
      ),
  },
  { title: '接收者', key: 'recipient', className: 'mono muted', ellipsis: { tooltip: true } },
  {
    title: '事件类型',
    key: 'event_types',
    className: 'mono faint',
    render: (r) => (r.event_types ?? []).join(', '),
  },
]
</script>

<template>
  <panel-card title="推送路由">
    <template #header>
      <n-tag size="small" :bordered="false">{{ routes.length }} 条</n-tag>
      <span class="grow" />
      <n-select
        v-model:value="filter"
        :options="groupOptions()"
        size="small"
        style="width: 220px"
        @update:value="load"
      />
    </template>
    <n-data-table :columns="columns" :data="routes" :bordered="false" size="small" />
  </panel-card>
</template>
