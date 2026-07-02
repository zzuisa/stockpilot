<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { NButton, NModal, NInput, NSelect, NTag, NPopover } from 'naive-ui'
import { accountsApi } from '@/api/endpoints'
import type { T212Account } from '@/api/types'
import { apiError } from '@/api/client'
import { useNotify } from '@/composables/useNotify'

const notify = useNotify()
const accounts = ref<T212Account[]>([])
const showManage = ref(false)
const loading = ref(false)

// 新建表单
const newName = ref('')
const newKey = ref('')
const newSecret = ref('')
const newEnv = ref<'demo' | 'live'>('demo')
const creating = ref(false)

// 编辑表单
const editId = ref<number | null>(null)
const editName = ref('')
const editKey = ref('')
const editSecret = ref('')
const editEnv = ref<'demo' | 'live'>('demo')
const saving = ref(false)

const activeAccount = computed(() => accounts.value.find((a) => a.is_active) ?? null)
const others = computed(() => accounts.value.filter((a) => !a.is_active))

async function load() {
  accounts.value = await accountsApi.list()
}

async function activate(id: number) {
  loading.value = true
  try {
    await accountsApi.activate(id)
    await load()
    notify.ok('账户已切换，请刷新 T212 数据')
  } catch (e) {
    notify.err(`切换失败: ${apiError(e)}`)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!newName.value.trim() || !newKey.value.trim()) return
  creating.value = true
  try {
    await accountsApi.create({
      name: newName.value,
      api_key: newKey.value,
      api_secret: newSecret.value.trim() || undefined,
      env: newEnv.value,
    })
    notify.ok('账户已添加')
    newName.value = ''
    newKey.value = ''
    newSecret.value = ''
    newEnv.value = 'demo'
    await load()
  } catch (e) {
    notify.err(`添加失败: ${apiError(e)}`)
  } finally {
    creating.value = false
  }
}

function startEdit(a: T212Account) {
  editId.value = a.id
  editName.value = a.name
  editKey.value = ''
  editSecret.value = ''
  editEnv.value = a.env
}
function cancelEdit() {
  editId.value = null
}

async function saveEdit() {
  if (!editId.value) return
  saving.value = true
  try {
    const body: { name?: string; api_key?: string; api_secret?: string; env?: 'demo' | 'live' } = {
      name: editName.value,
      env: editEnv.value,
    }
    if (editKey.value.trim()) body.api_key = editKey.value.trim()
    // 填了 secret 则更新；填空字符串则清除；不填则不传（后端不修改）
    if (editSecret.value !== '') body.api_secret = editSecret.value.trim()
    await accountsApi.update(editId.value, body)
    notify.ok('账户已更新')
    editId.value = null
    await load()
  } catch (e) {
    notify.err(`保存失败: ${apiError(e)}`)
  } finally {
    saving.value = false
  }
}

async function remove(id: number, name: string) {
  if (!confirm(`确认删除账户「${name}」？`)) return
  try {
    await accountsApi.remove(id)
    notify.ok('已删除')
    await load()
  } catch (e) {
    notify.err(`删除失败: ${apiError(e)}`)
  }
}

onMounted(load)
</script>

<template>
  <!-- 顶栏 compact 显示：当前账户 + 切换弹出 -->
  <div class="switcher">
    <n-popover trigger="click" placement="bottom-end" :show-arrow="false">
      <template #trigger>
        <button class="active-btn">
          <span class="dot" :class="activeAccount?.env ?? 'demo'" />
          <span class="name">{{ activeAccount?.name ?? '未配置账户' }}</span>
          <n-tag
            v-if="activeAccount"
            size="tiny"
            :type="activeAccount.env === 'live' ? 'error' : 'info'"
            :bordered="false"
            style="margin-left:4px"
          >
            {{ activeAccount.env.toUpperCase() }}
          </n-tag>
          <span class="caret">▾</span>
        </button>
      </template>

      <div class="pop">
        <div class="pop-title">切换 T212 账户</div>

        <!-- 其他账户列表 -->
        <div v-if="others.length" class="acct-list">
          <div
            v-for="a in others"
            :key="a.id"
            class="acct-row"
            @click="activate(a.id)"
          >
            <span class="dot" :class="a.env" />
            <span class="acct-name">{{ a.name }}</span>
            <n-tag size="tiny" :type="a.env === 'live' ? 'error' : 'info'" :bordered="false">
              {{ a.env.toUpperCase() }}
            </n-tag>
          </div>
        </div>
        <div v-else class="faint small" style="margin-bottom:8px">无其他账户</div>

        <n-button size="small" block @click="showManage = true">
          管理账户 / 添加新账户
        </n-button>
      </div>
    </n-popover>
  </div>

  <!-- 管理 Modal -->
  <n-modal v-model:show="showManage" preset="card" style="width:500px" title="T212 账户管理" :bordered="false">
    <!-- 账户列表 -->
    <div class="acct-mgr-list">
      <div v-for="a in accounts" :key="a.id" class="mgr-row">
        <template v-if="editId !== a.id">
          <span class="dot" :class="a.env" style="margin-right:6px" />
          <span class="mgr-name">{{ a.name }}</span>
          <n-tag size="tiny" :type="a.env === 'live' ? 'error' : 'info'" :bordered="false" style="margin-right:4px">
            {{ a.env.toUpperCase() }}
          </n-tag>
          <span class="key-hint faint small">{{ a.api_key_hint }}</span>
          <span v-if="a.has_secret" class="secret-badge faint small" title="已配置 API Secret">+S</span>
          <span v-if="a.is_active" class="active-badge">当前</span>
          <div class="mgr-actions">
            <n-button size="tiny" quaternary @click="startEdit(a)">编辑</n-button>
            <n-button v-if="!a.is_active" size="tiny" type="primary" secondary @click="activate(a.id)">激活</n-button>
            <n-button v-if="!a.is_active" size="tiny" type="error" quaternary @click="remove(a.id, a.name)">删除</n-button>
          </div>
        </template>

        <!-- 行内编辑 -->
        <template v-else>
          <div class="edit-form">
            <div class="two-col-edit">
              <n-input v-model:value="editName" size="small" placeholder="账户名称" />
              <n-select
                v-model:value="editEnv"
                size="small"
                :options="[{ label: 'Demo', value: 'demo' }, { label: 'Live', value: 'live' }]"
              />
            </div>
            <n-input
              v-model:value="editKey"
              size="small"
              placeholder="新 API Key（留空不修改）"
              type="password"
              show-password-on="click"
              style="margin-bottom:6px"
            />
            <n-input
              v-model:value="editSecret"
              size="small"
              placeholder="API Secret（留空不修改；填空格清除）"
              type="password"
              show-password-on="click"
            />
            <div class="edit-btns">
              <n-button size="tiny" type="primary" :loading="saving" @click="saveEdit">保存</n-button>
              <n-button size="tiny" @click="cancelEdit">取消</n-button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <div class="divider" />

    <!-- 新建账户 -->
    <div class="new-form">
      <div class="section-title">添加账户</div>
      <div class="two-col-edit">
        <n-input v-model:value="newName" size="small" placeholder="账户名称，如「Demo 个人」" />
        <n-select
          v-model:value="newEnv"
          size="small"
          :options="[{ label: 'Demo 模拟盘', value: 'demo' }, { label: 'Live 实盘', value: 'live' }]"
        />
      </div>
      <n-input
        v-model:value="newKey"
        size="small"
        placeholder="API Key（必填）"
        type="password"
        show-password-on="click"
        style="margin-bottom:6px"
      />
      <n-input
        v-model:value="newSecret"
        size="small"
        placeholder="API Secret（可选，Base64 鉴权用）"
        type="password"
        show-password-on="click"
        style="margin-bottom:8px"
      />
      <n-button
        type="primary"
        size="small"
        block
        :loading="creating"
        :disabled="!newName.trim() || !newKey.trim()"
        @click="create"
      >
        添加账户
      </n-button>
    </div>
  </n-modal>
</template>

<style scoped>
.switcher { display: flex; align-items: center; }

.active-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 4px 10px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text);
  transition: border-color 0.15s;
}
.active-btn:hover { border-color: var(--amber-dim); }
.name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caret { font-size: 10px; color: var(--faint); margin-left: 2px; }

.dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.dot.demo { background: var(--info, #3498db); }
.dot.live { background: var(--down, #e74c3c); }

.pop { min-width: 220px; padding: 4px 0; }
.pop-title { font-size: 11px; color: var(--faint); padding: 0 4px 8px; letter-spacing: 0.05em; }

.acct-list { margin-bottom: 8px; }
.acct-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.acct-row:hover { background: rgba(232,163,61,0.07); }
.acct-name { flex: 1; }

/* 管理 Modal */
.acct-mgr-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.mgr-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 5px;
  font-size: 12px;
}
.mgr-name { flex: 1; font-weight: 500; }
.key-hint { margin-right: 4px; }
.secret-badge { margin-right: auto; letter-spacing: 0; }
.active-badge {
  font-size: 10px;
  background: rgba(61,214,140,0.15);
  color: var(--up);
  padding: 1px 5px;
  border-radius: 3px;
}
.mgr-actions { display: flex; gap: 4px; }

.edit-form { width: 100%; }
.two-col-edit { display: grid; grid-template-columns: 1fr 120px; gap: 6px; margin-bottom: 6px; }
.edit-btns { display: flex; gap: 6px; margin-top: 6px; }

.divider { height: 1px; background: var(--line); margin: 8px 0 12px; }
.new-form {}
.section-title { font-size: 11px; color: var(--faint); letter-spacing: 0.05em; margin-bottom: 8px; }

.faint { color: var(--faint); }
.small { font-size: 11px; }
</style>
