<script setup lang="ts">
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'

use([CanvasRenderer, LineChart, BarChart, PieChart, GridComponent, TooltipComponent,
  LegendComponent, MarkLineComponent, MarkPointComponent])

defineProps<{ option: EChartsOption; height?: string }>()
</script>

<template>
  <v-chart class="chart" :option="option" :style="{ height: height || '240px' }" autoresize />
</template>

<style scoped>
.chart {
  width: 100%;
}
</style>
