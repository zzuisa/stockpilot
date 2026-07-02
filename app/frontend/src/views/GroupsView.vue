<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckboxGroup,
  NCheckbox,
  NDataTable,
  NInput,
  NModal,
  NSelect,
  NSwitch,
  NTag,
  useDialog,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { groupsApi, notifyApi } from '@/api/endpoints'
import type { Group, GroupDetail, GroupRecipient, GroupSymbol, NotifyRoute } from '@/api/types'
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

// 单股新闻自动拉取配置
const NEWS_SOURCE_OPTIONS = [
  { label: 'Finnhub', value: 'finnhub' },
  { label: 'AlphaVantage', value: 'alphavantage' },
]
const NEWS_TYPE_OPTIONS = [
  { label: '财报/业绩', value: 'earnings' },
  { label: '公司公告', value: 'announcement' },
  { label: '行业/竞争', value: 'industry' },
  { label: '宏观经济', value: 'macro' },
  { label: '监管/地缘', value: 'regulatory' },
  { label: '分析师评级', value: 'analyst' },
  { label: '供应链/运营', value: 'supply_chain' },
  { label: '公司治理', value: 'governance' },
]
const showNews = ref(false)
const newsSym = ref('')
const newsCfg = ref<{ news_auto: boolean; news_sources: string[]; news_types: string[] }>({
  news_auto: false,
  news_sources: ['finnhub', 'alphavantage'],
  news_types: [],
})

function openNewsConfig(s: GroupSymbol) {
  const cfg = (s.symbol_config ?? {}) as Record<string, unknown>
  newsSym.value = s.symbol
  newsCfg.value = {
    news_auto: Boolean(cfg.news_auto),
    news_sources: Array.isArray(cfg.news_sources) && cfg.news_sources.length
      ? (cfg.news_sources as string[])
      : ['finnhub', 'alphavantage'],
    news_types: Array.isArray(cfg.news_types) ? (cfg.news_types as string[]) : [],
  }
  showNews.value = true
}

async function saveNewsConfig() {
  if (!detail.value) return
  try {
    await groupsApi.setSymbolNews(detail.value.id, newsSym.value, newsCfg.value)
    notify.ok(`${newsSym.value} 新闻配置已保存`)
    showNews.value = false
    await selectGroup({ id: detail.value.id } as Group)
  } catch (e) {
    notify.err(`保存失败: ${apiError(e)}`)
  }
}

// 推送接收人矩阵（每接收人独立事件类型）
const recipients = ref<GroupRecipient[]>([])
const routesPreview = ref<NotifyRoute[]>([])

const EVENT_TYPES = [
  { label: '日报', value: 'daily_report' },
  { label: '信号', value: 'signal' },
  { label: '新闻冲击', value: 'news_shock' },
  { label: '成交结果', value: 'order_result' },
]
const CHANNEL_OPTIONS = [
  { label: 'Telegram', value: 'telegram' },
  { label: 'Email', value: 'email' },
]

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
    recipients.value = buildRecipients(d)
    await loadRoutes(g.id)
  } catch (e) {
    notify.err(`加载分组详情失败: ${apiError(e)}`)
  }
}

/** 从 config 还原接收人矩阵：优先 recipients，否则由旧字段合成 */
function buildRecipients(d: GroupDetail): GroupRecipient[] {
  const cfg = d.config ?? {}
  if (cfg.recipients?.length) {
    return cfg.recipients.map((r) => ({
      channel: r.channel, recipient: r.recipient, events: [...(r.events ?? [])],
    }))
  }
  const on = cfg.notify_on ?? ['daily_report', 'signal']
  const out: GroupRecipient[] = []
  for (const r of cfg.telegram_chat_ids ?? []) out.push({ channel: 'telegram', recipient: r, events: [...on] })
  for (const r of cfg.email_recipients ?? []) out.push({ channel: 'email', recipient: r, events: [...on] })
  return out
}

async function loadRoutes(gid: string) {
  try {
    routesPreview.value = await notifyApi.routes(gid)
  } catch {
    routesPreview.value = []
  }
}

function addRecipient() {
  recipients.value.push({ channel: 'telegram', recipient: '', events: ['daily_report', 'signal'] })
}
function removeRecipient(i: number) {
  recipients.value.splice(i, 1)
}

async function saveNotify() {
  if (!detail.value) return
  const clean = recipients.value
    .map((r) => ({ ...r, recipient: r.recipient.trim() }))
    .filter((r) => r.recipient)
  try {
    await groupsApi.setNotify(detail.value.id, clean)
    notify.ok('推送配置已保存')
    await loadRoutes(detail.value.id)
    await reloadGroups()
  } catch (e) {
    notify.err(`保存失败: ${apiError(e)}`)
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
    title: '新闻',
    key: 'news',
    width: 96,
    render: (s) => {
      const on = Boolean((s.symbol_config as Record<string, unknown> | undefined)?.news_auto)
      return h(
        NButton,
        { size: 'tiny', secondary: true, type: on ? 'success' : 'default', onClick: () => openNewsConfig(s) },
        { default: () => (on ? '自动 ✓' : '未开启') },
      )
    },
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

        <panel-card :title="`推送接收人 (${recipients.length})`">
          <template #header>
            <span class="grow" />
            <n-button size="tiny" type="primary" secondary @click="saveNotify">保存推送</n-button>
          </template>
          <div v-if="!recipients.length" class="faint" style="font-size: 12px; padding: 6px 0">
            暂无接收人，点下方「+ 接收人」添加
          </div>
          <div v-for="(r, i) in recipients" :key="i" class="recip-row">
            <n-select v-model:value="r.channel" :options="CHANNEL_OPTIONS" size="small" style="width: 110px" />
            <n-input
              v-model:value="r.recipient"
              size="small"
              :placeholder="r.channel === 'telegram' ? 'Chat ID' : 'email@x.com'"
              style="flex: 1; min-width: 130px"
            />
            <n-checkbox-group v-model:value="r.events" class="ev-group">
              <n-checkbox v-for="e in EVENT_TYPES" :key="e.value" :value="e.value" :label="e.label" />
            </n-checkbox-group>
            <n-button size="tiny" type="error" quaternary @click="removeRecipient(i)">✕</n-button>
          </div>
          <n-button size="small" dashed block style="margin-top: 8px" @click="addRecipient">
            + 接收人
          </n-button>
        </panel-card>

        <panel-card :title="`生效路由预览 (${routesPreview.length})`">
          <div v-if="!routesPreview.length" class="faint" style="font-size: 12px">
            保存推送后将按接收人 × 事件类型展开为路由
          </div>
          <table v-else class="routes-tbl">
            <thead>
              <tr><th>标的</th><th>渠道</th><th>接收人</th><th>事件类型</th></tr>
            </thead>
            <tbody>
              <tr v-for="rt in routesPreview" :key="rt.id">
                <td>{{ rt.symbol || '(组级)' }}</td>
                <td>
                  <n-tag size="tiny" :bordered="false" :type="rt.channel === 'telegram' ? 'success' : 'warning'">
                    {{ rt.channel }}
                  </n-tag>
                </td>
                <td class="mono muted ellip">{{ rt.recipient }}</td>
                <td class="faint mono">{{ (rt.event_types || []).join(', ') }}</td>
              </tr>
            </tbody>
          </table>
        </panel-card>
      </template>
    </div>

    <!-- 单股新闻自动拉取配置弹窗 -->
    <n-modal
      v-model:show="showNews"
      preset="card"
      :title="`新闻自动拉取 · ${newsSym}`"
      style="width: 460px"
      :bordered="false"
    >
      <div class="field news-switch">
        <label>自动拉取并推送精华</label>
        <n-switch v-model:value="newsCfg.news_auto" />
      </div>
      <div class="field">
        <label>采集来源</label>
        <n-checkbox-group v-model:value="newsCfg.news_sources" class="ev-group">
          <n-checkbox v-for="o in NEWS_SOURCE_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
        </n-checkbox-group>
      </div>
      <div class="field">
        <label>关注类别（不选 = 全部）</label>
        <n-checkbox-group v-model:value="newsCfg.news_types" class="ev-group">
          <n-checkbox v-for="o in NEWS_TYPE_OPTIONS" :key="o.value" :value="o.value" :label="o.label" />
        </n-checkbox-group>
      </div>
      <div class="faint" style="font-size: 12px; margin-top: 6px">
        开启后，系统按所选来源/类别拉取该股新闻，经 LLM 高信号筛选后，仅推送「精华总结 + 投资判断」。
      </div>
      <template #footer>
        <div class="flex gap-8">
          <n-button type="primary" @click="saveNewsConfig">保存</n-button>
          <n-button quaternary @click="showNews = false">取消</n-button>
        </div>
      </template>
    </n-modal>

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
.news-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.news-switch label {
  margin-bottom: 0;
}
.events-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
}
.recip-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 0;
  border-bottom: 1px solid var(--panel2);
}
.recip-row:last-of-type {
  border-bottom: none;
}
.ev-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.routes-tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.routes-tbl th {
  text-align: left;
  color: var(--faint);
  font-weight: 500;
  font-size: 11px;
  padding: 5px 6px;
  border-bottom: 1px solid var(--line);
}
.routes-tbl td {
  padding: 5px 6px;
  border-bottom: 1px solid var(--panel2);
}
.ellip {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
