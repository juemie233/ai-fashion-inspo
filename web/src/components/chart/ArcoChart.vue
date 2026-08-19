<script setup lang="ts">
/**
 * 通用图表容器（Arco Design Pro 风格封装）：props 传入 echarts option，
 * 内部统一处理 init / setOption（深监听自动重绘）/ resize / dispose 与加载/空态。
 * 使用方只需提供 option，无需关心 echarts 生命周期。
 */

import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart, GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GraphChart,
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
    /** 图表实例就绪回调（用于绑定 echarts 事件，如 graph 图节点点击） */
    onReady?: (chart: echarts.ECharts) => void
  }>(),
  { height: 320, loading: false, emptyText: '暂无数据' },
)

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let readyFired = false

/** 惰性初始化图表实例（容器挂载后 / 被销毁后重建） */
function ensureChart(): echarts.ECharts | null {
  if (!chartEl.value) return null
  if (!chart || chart.isDisposed()) {
    chart = echarts.init(chartEl.value)
    readyFired = false
  }
  return chart
}

/** 渲染（notMerge 清空旧数据，避免筛选变化后残留上一份序列）。
 * 需在 nextTick 后执行：option 从 null → 对象时 v-show 容器刚变为可见，
 * 立即 init 会拿到 0 尺寸容器导致图表不可见。 */
function render() {
  if (!props.option) return
  const c = ensureChart()
  if (c) {
    c.setOption(props.option, true)
    // 实例就绪后通知使用方绑定事件（仅在每次新 init 后触发一次）
    if (!readyFired) {
      readyFired = true
      props.onReady?.(c)
    }
  }
}

watch(
  () => props.option,
  () => {
    nextTick(render)
  },
  { deep: true },
)

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
    <a-spin :loading="loading" class="arco-chart-spin">
      <!-- v-show 而非 v-if：容器常驻 DOM，保证 init 时实例可绑定且后续尺寸正常 -->
      <div v-show="option" ref="chartEl" class="arco-chart-canvas" />
      <a-empty v-if="!option" :description="emptyText" />
    </a-spin>
  </div>
</template>

<style scoped>
.arco-chart {
  width: 100%;
}
/* a-spin 内部 children 容器高度塌陷：显式撑满，canvas 的 height:100% 才能生效 */
.arco-chart-spin,
.arco-chart :deep(.arco-spin-children) {
  width: 100%;
  height: 100%;
}
.arco-chart-canvas {
  width: 100%;
  height: 100%;
}
</style>
