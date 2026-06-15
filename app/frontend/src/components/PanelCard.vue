<script setup lang="ts">
import { NCard } from 'naive-ui'
defineProps<{ title?: string }>()
</script>

<template>
  <n-card :bordered="true" class="panel-card" content-style="padding:14px">
    <template v-if="title || $slots.header" #header>
      <div class="panel-head">
        <span class="section-label">{{ title }}</span>
        <slot name="header" />
      </div>
    </template>
    <slot />
  </n-card>
</template>

<style scoped>
.panel-card {
  margin-bottom: 14px;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
