<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NBadge } from 'naive-ui'
import { NAV_ITEMS } from '@/router'
import { useClock } from '@/composables/useClock'
import { useSystemStore } from '@/stores/system'

const route = useRoute()
const router = useRouter()
const { clock } = useClock()
const system = useSystemStore()

onMounted(() => {
  system.loadHealth()
  setInterval(() => system.loadHealth(), 30000)
})

function go(name: string) {
  router.push({ name })
}
</script>

<template>
  <div class="wrap">
    <!-- 顶部 -->
    <header class="app-header">
      <div class="logo">STOCK<span>/</span>PILOT</div>
      <span class="env-chip">MANAGE</span>
      <span v-if="system.health" class="health-inline mono">
        <n-badge dot :type="system.health.db ? 'success' : 'error'" /> DB
        <n-badge dot :type="system.health.scheduler ? 'success' : 'error'" style="margin-left: 10px" />
        Scheduler
      </span>
      <span class="grow" />
      <span class="clock mono">{{ clock }}</span>
    </header>

    <!-- 导航 -->
    <nav class="app-nav">
      <button
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :class="['nav-btn', { on: route.name === item.name }]"
        @click="go(item.name)"
      >
        {{ item.label }}
      </button>
    </nav>

    <!-- 内容 -->
    <router-view v-slot="{ Component }">
      <keep-alive :include="['OverviewView']">
        <component :is="Component" />
      </keep-alive>
    </router-view>
  </div>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  padding: 6px 0 14px;
  border-bottom: 1px solid var(--line);
}
.env-chip {
  font-family: var(--mono);
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid var(--amber-dim);
  color: var(--amber);
  border-radius: 3px;
}
.health-inline {
  font-size: 12px;
  color: var(--muted);
  align-self: center;
}
.clock {
  font-size: 12px;
  color: var(--muted);
  align-self: center;
}
.app-nav {
  display: flex;
  gap: 4px;
  margin: 16px 0;
  overflow-x: auto;
}
.nav-btn {
  font-family: var(--sans);
  font-size: 13px;
  padding: 8px 16px;
  background: none;
  border: 1px solid var(--line);
  color: var(--muted);
  border-radius: 5px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.nav-btn:hover {
  color: var(--text);
  border-color: var(--line2);
}
.nav-btn.on {
  color: var(--amber);
  border-color: var(--amber-dim);
  background: rgba(232, 163, 61, 0.06);
}
</style>
