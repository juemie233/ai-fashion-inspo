<script setup lang="ts">
/**
 * 通用图表容器（Arco Design Pro 风格封装）：props 传入 echarts option，
 * 内部统一处理 init / setOption（深监听自动重绘）/ resize / dispose 与加载/空态。
 * 使用方只需提供 option，无需关心 echarts 生命周期。
 */

import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  CanvasRenderer,
])

const props = withDefaults(
  defineProps<{
    /** ECharts 配置项（深监听，变化时自动重绘；null 时显示空态） */
    option: EChartsOption | null
    /** 图表高度（px） */
    height?: number
    /** 加载态 */
    loading?: boolean
    /** 空态文案（option 为 null 时展示） */
    emptyText?: string
  }>(),
  { height: 320, loading: false, emptyText: '暂无数据' },
)

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

/** 惰性初始化图表实例（容器挂载后 / 被销毁后重建） */
function ensureChart(): echarts.ECharts | null {
  if (!chartEl.value) return null
  if (!chart || chart.isDisposed()) {
    chart = echarts.init(chartEl.value)
  }
  return chart
}

/** 渲染（notMerge 清空旧数据，避免筛选变化后残留上一份序列） */
function render() {
  if (!props.option) return
  const c = ensureChart()
  if (c) c.setOption(props.option, true)
}

watch(() => props.option, render, { deep: true })

function handleResize() {
  chart?.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="arco-chart" :style="{ height: `${height}px` }">
    <a-spin :loading="loading" style="width: 100%; height: 100%">
      <div v-if="option" ref="chartEl" class="arco-chart-canvas" />
      <a-empty v-else :description="emptyText" />
    </a-spin>
  </div>
</template>

<style scoped>
.arco-chart {
  width: 100%;
}
.arco-chart-canvas {
  width: 100%;
  height: 100%;
}
</style>
