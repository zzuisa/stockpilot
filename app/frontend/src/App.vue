<script setup lang="ts">
import { darkTheme, NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import { themeOverrides } from './theme'
import AppShell from './components/AppShell.vue'
import { zhCN, dateZhCN } from 'naive-ui'
</script>

<template>
  <n-config-provider
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider :max="4" placement="bottom-right">
      <n-dialog-provider>
        <app-shell />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
