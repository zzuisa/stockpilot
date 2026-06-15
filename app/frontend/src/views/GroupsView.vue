<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckboxGroup,
  NCheckbox,
  NDataTable,
  NDynamicTags,
  NInput,
  NModal,
  useDialog,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { groupsApi } from '@/api/endpoints'
import type { Group, GroupDetail, GroupSymbol } from '@/api/types'
import { apiError } from '@/api/client'
import { useGroupsStore } from '@/stores/groups'
import { useNotify } from '@/composables/useNotify'
import PanelCard from '@/components/PanelCard.vue'

defineOptions({ name: 'GroupsView' })

const notify = useNotify()
const dialog = useDialog()
const store = useGroupsStore()

const selId = ref<string | null>(null)
const detail = ref<GroupDetail | null>(null)
const editName = ref('')
const editDesc = ref('')

// 新建分组
const showNew = ref(false)
const newG = ref({ id: '', name: '', description: '' })

// 标的添加
const newSym = ref({ symbol: '', t212_ticker: '' })

// 接收者
const tgList = ref<string[]>([])
const tgEvents = ref<string[]>(['daily_report', 'signal'])
const emailList = ref<string[]>([])
const emailEvents = ref<string[]>(['daily_report'])

async function reloadGroups() {
  await store.load(true)
}

async function selectGroup(g: Group) {
  selId.value = g.id
  showNew.value = false
  try {
    const d = await groupsApi.detail(g.id)
    detail.value = d
    editName.value = d.name
    editDesc.value = d.description ?? ''
    const cfg = d.config ?? {}
    tgList.value = [...(cfg.telegram_chat_ids ?? [])]
    emailList.value = [...(cfg.email_recipients ?? [])]
    tgEvents.value = [...(cfg.notify_on ?? ['daily_report', 'signal'])]
    emailEvents.value = [...(cfg.notify_on ?? ['daily_report'])]
  } catch (e) {
    notify.err(`加载分组详情失败: ${apiError(e)}`)
  }
}

function openNew() {
  showNew.value = true
  selId.value = null
  detail.value = null
  newG.value = { id: '', name: '', description: '' }
}

async function createGroup() {
  try {
    await groupsApi.create(newG.value)
    notify.ok('分组已创建')
    showNew.value = false
    await reloadGroups()
    const g = store.groups.find((x) => x.id === newG.value.id)
    if (g) await selectGroup(g)
  } catch (e) {
    notify.err(`创建失败: ${apiError(e)}`)
  }
}

async function saveGroup() {
  if (!detail.value) return
  try {
    await groupsApi.update(detail.value.id, {
      id: detail.value.id,
      name: editName.value,
      description: editDesc.value,
      ...(detail.value.config ?? {}),
    })
    notify.ok('已保存')
    await reloadGroups()
  } catch (e) {
    notify.err(`保存失败: ${apiError(e)}`)
  }
}

function deleteGroup() {
  if (!detail.value) return
  const id = detail.value.id
  dialog.warning({
    title: '删除分组',
    content: `确认删除分组 ${id}？此操作不可撤销。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await groupsApi.remove(id)
        notify.ok('已删除')
        detail.value = null
        selId.value = null
        await reloadGroups()
      } catch (e) {
        notify.err(`删除失败: ${apiError(e)}`)
      }
    },
  })
}

async function addSymbol() {
  if (!detail.value || !newSym.value.symbol) return
  try {
    await groupsApi.addSymbol(detail.value.id, {
      symbol: newSym.value.symbol.toUpperCase(),
      t212_ticker: newSym.value.t212_ticker || null,
    })
    notify.ok('标的已添加')
    newSym.value = { symbol: '', t212_ticker: '' }
    await selectGroup({ id: detail.value.id } as Group)
    await reloadGroups()
  } catch (e) {
    notify.err(`添加失败: ${apiError(e)}`)
  }
}

function removeSymbol(sym: string) {
  if (!detail.value) return
  const gid = detail.value.id
  dialog.warning({
    title: '移除标的',
    content: `从分组 ${gid} 移除 ${sym}？`,
    positiveText: '移除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await groupsApi.removeSymbol(gid, sym)
        notify.ok(`已移除 ${sym}`)
        await selectGroup({ id: gid } as Group)
        await reloadGroups()
      } catch (e) {
        notify.err(`移除失败: ${apiError(e)}`)
      }
    },
  })
}

async function saveRecipients(channel: 'telegram' | 'email') {
  if (!detail.value) return
  try {
    await groupsApi.setRecipients(detail.value.id, {
      channel,
      recipients: channel === 'telegram' ? tgList.value : emailList.value,
      event_types: channel === 'telegram' ? tgEvents.value : emailEvents.value,
    })
    notify.ok(`${channel === 'telegram' ? 'Telegram' : 'Email'} 接收者已保存`)
    await reloadGroups()
  } catch (e) {
    notify.err(`保存失败: ${apiError(e)}`)
  }
}

async function syncYaml() {
  try {
    await groupsApi.syncYaml()
    notify.ok('YAML 已同步到数据库')
    await reloadGroups()
    if (selId.value) {
      const g = store.groups.find((x) => x.id === selId.value)
      if (g) await selectGroup(g)
    }
  } catch (e) {
    notify.err(`同步失败: ${apiError(e)}`)
  }
}

async function exportYaml() {
  try {
    const text = await groupsApi.exportYaml()
    const blob = new Blob([text], { type: 'text/yaml' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'watchlist_export.yaml'
    a.click()
    notify.ok('YAML 已下载')
  } catch (e) {
    notify.err(`导出失败: ${apiError(e)}`)
  }
}

onMounted(reloadGroups)

const symColumns: DataTableColumns<GroupSymbol> = [
  {
    title: 'SYMBOL',
    key: 'symbol',
    render: (s) =>
      h('span', { class: 'tag-amber' }, s.symbol),
  },
  { title: 'T212 Ticker', key: 't212_ticker', className: 'muted', render: (s) => s.t212_ticker || '—' },
  {
    title: '标签',
    key: 'tags',
    render: (s) => (s.tags ?? []).join(', ') || '—',
  },
  {
    title: '操作',
    key: 'action',
    align: 'right',
    render: (s) =>
      h(
        NButton,
        { size: 'tiny', type: 'error', secondary: true, onClick: () => removeSymbol(s.symbol) },
        { default: () => '✕' },
      ),
  },
]
</script>

<template>
  <div class="groups-grid">
    <!-- 左：分组列表 -->
    <div>
      <panel-card>
        <template #header>
          <span class="section-label">分组列表</span>
          <span class="grow" />
          <n-button size="tiny" type="primary" secondary @click="openNew">+ 新建</n-button>
        </template>
        <div v-if="!store.groups.length" class="faint" style="padding: 8px 4px; font-size: 12px">
          暂无分组
        </div>
        <div
          v-for="g in store.groups"
          :key="g.id"
          :class="['group-item', { sel: selId === g.id }]"
          @click="selectGroup(g)"
        >
          <div>
            <div class="gname">{{ g.name }}</div>
            <div class="gid mono">{{ g.id }}</div>
          </div>
          <span class="gcnt mono">{{ g.symbol_count ?? g.config?.symbols?.length ?? 0 }}只</span>
        </div>
        <div class="yaml-actions">
          <n-button size="small" quaternary block @click="syncYaml">⟳ 从 YAML 同步</n-button>
          <n-button size="small" quaternary block @click="exportYaml">↓ 导出 YAML</n-button>
        </div>
      </panel-card>
    </div>

    <!-- 右：详情 -->
    <div>
      <panel-card v-if="!detail && !showNew">
        <div class="empty">← 选择左侧分组开始管理</div>
      </panel-card>

      <template v-if="detail">
        <panel-card title="分组信息">
          <template #header>
            <span class="grow" />
            <span class="mono amber" style="font-size: 11px">{{ detail.id }}</span>
          </template>
          <div class="field">
            <label>名称</label>
            <n-input v-model:value="editName" />
          </div>
          <div class="field">
            <label>描述</label>
            <n-input v-model:value="editDesc" />
          </div>
          <div class="flex gap-8">
            <n-button size="small" type="primary" secondary @click="saveGroup">保存</n-button>
            <n-button size="small" type="error" secondary @click="deleteGroup">删除组</n-button>
          </div>
        </panel-card>

        <panel-card :title="`标的 (${detail.symbols.length})`">
          <n-data-table :columns="symColumns" :data="detail.symbols" :bordered="false" size="small" />
          <div class="add-row">
            <n-input
              v-model:value="newSym.symbol"
              placeholder="SYMBOL (如 NVDA)"
              style="text-transform: uppercase"
              @keyup.enter="addSymbol"
            />
            <n-input v-model:value="newSym.t212_ticker" placeholder="T212 Ticker (可选)" />
            <n-button size="small" type="primary" secondary :disabled="!newSym.symbol" @click="addSymbol">
              + 添加
            </n-button>
          </div>
        </panel-card>

        <panel-card :title="`Telegram 接收者 (${tgList.length})`">
          <n-dynamic-tags v-model:value="tgList" />
          <div class="events-row">
            <span class="faint">推送事件:</span>
            <n-checkbox-group v-model:value="tgEvents">
              <n-checkbox value="daily_report" label="日报" />
              <n-checkbox value="signal" label="信号" />
              <n-checkbox value="order_result" label="成交" />
            </n-checkbox-group>
            <n-button size="tiny" quaternary style="margin-left: auto" @click="saveRecipients('telegram')">
              保存
            </n-button>
          </div>
        </panel-card>

        <panel-card :title="`Email 接收者 (${emailList.length})`">
          <n-dynamic-tags v-model:value="emailList" />
          <div class="events-row">
            <span class="faint">推送事件:</span>
            <n-checkbox-group v-model:value="emailEvents">
              <n-checkbox value="daily_report" label="日报" />
              <n-checkbox value="signal" label="信号" />
            </n-checkbox-group>
            <n-button size="tiny" quaternary style="margin-left: auto" @click="saveRecipients('email')">
              保存
            </n-button>
          </div>
        </panel-card>
      </template>
    </div>

    <!-- 新建分组弹窗 -->
    <n-modal
      v-model:show="showNew"
      preset="card"
      title="新建分组"
      style="width: 440px"
      :bordered="false"
    >
      <div class="field">
        <label>ID（英文小写/下划线）</label>
        <n-input v-model:value="newG.id" placeholder="core_holdings" />
      </div>
      <div class="field">
        <label>名称</label>
        <n-input v-model:value="newG.name" placeholder="核心持仓" />
      </div>
      <div class="field">
        <label>描述</label>
        <n-input v-model:value="newG.description" placeholder="长期持有的核心股票" />
      </div>
      <template #footer>
        <div class="flex gap-8">
          <n-button type="primary" :disabled="!newG.id || !newG.name" @click="createGroup">创建</n-button>
          <n-button quaternary @click="showNew = false">取消</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.groups-grid {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 14px;
  align-items: start;
}
@media (max-width: 720px) {
  .groups-grid {
    grid-template-columns: 1fr;
  }
}
.group-item {
  padding: 9px 12px;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.group-item:hover {
  background: rgba(255, 255, 255, 0.04);
}
.group-item.sel {
  background: rgba(232, 163, 61, 0.08);
}
.group-item.sel .gname {
  color: var(--amber);
}
.gname {
  font-weight: 500;
  font-size: 13px;
}
.gid {
  font-size: 11px;
  color: var(--faint);
}
.gcnt {
  font-size: 11px;
  color: var(--faint);
}
.yaml-actions {
  border-top: 1px solid var(--line);
  margin-top: 10px;
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.empty {
  color: var(--faint);
  font-size: 13px;
  text-align: center;
  padding: 32px;
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
.add-row {
  display: flex;
  gap: 6px;
  margin-top: 12px;
}
.events-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
}
:deep(.tag-amber) {
  font-family: var(--mono);
  font-size: 12px;
  background: rgba(232, 163, 61, 0.12);
  color: var(--amber);
  padding: 2px 7px;
  border-radius: 3px;
}
</style>
