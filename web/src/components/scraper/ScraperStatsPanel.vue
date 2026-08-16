<script setup lang="ts">
/** 采集统计看板：总量概览 + 平台分布柱状图 + 每日趋势折线图（可折叠卡片）。 */

import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useScraperStats } from '@/composables/useScraperStats'
import { PLATFORM_LABELS } from '@/composables/useScraperTasks'

echarts.use([BarChart, LineChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const { stats, loading, loadStats } = useScraperStats()

/** 是否展开看板 */
const expanded = ref(false)

const platformRef = ref<HTMLDivElement | null>(null)
const dayRef = ref<HTMLDivElement | null>(null)
let platformChart: echarts.ECharts | null = null
let dayChart: echarts.ECharts | null = null

/** 展开时加载数据并渲染图表 */
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) {
    nextTick(() => setTimeout(async () => {
      await loadStats(30)
      nextTick(renderCharts)
    }, 30))
  }
}

async function refresh() {
  await loadStats(30)
  nextTick(renderCharts)
}

function renderCharts() {
  renderPlatform()
  renderDay()
}

function renderPlatform() {
  if (!stats.value || !platformRef.value) return
  if (!platformChart || platformChart.isDisposed()) platformChart = echarts.init(platformRef.value)
  const rows = stats.value.by_platform
  const names = rows.map(r => PLATFORM_LABELS[r.platform] || r.platform)
  platformChart.setOption({
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: names, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      { name: '任务数', type: 'bar', data: rows.map(r => r.tasks), itemStyle: { color: '#2a78d6' }, barMaxWidth: 32 },
      { name: '入库素材', type: 'bar', data: rows.map(r => r.added), itemStyle: { color: '#1baf7a' }, barMaxWidth: 32 },
    ],
  })
}

function renderDay() {
  if (!stats.value || !dayRef.value) return
  if (!dayChart || dayChart.isDisposed()) dayChart = echarts.init(dayRef.value)
  const rows = stats.value.by_day
  dayChart.setOption({
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map(r => r.date.slice(5)), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      { name: '入库素材', type: 'line', smooth: true, data: rows.map(r => r.added), itemStyle: { color: '#1baf7a' }, areaStyle: { opacity: 0.08 } },
      { name: '失败任务', type: 'line', smooth: true, data: rows.map(r => r.failed), itemStyle: { color: '#d03050' } },
    ],
  })
}

function handleResize() {
  platformChart?.resize()
  dayChart?.resize()
}

onMounted(() => window.addEventListener('resize', handleResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  platformChart?.dispose(); platformChart = null
  dayChart?.dispose(); dayChart = null
})
</script>

<template>
<n-card size="small" style="margin-bottom:16px">
  <template #header>
    <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none" @click="toggle">
      <span>📊 采集统计（近 30 天）{{ expanded ? '▼' : '▶' }}</span>
      <n-button v-if="expanded" size="tiny" quaternary @click.stop="refresh">刷新</n-button>
    </div>
  </template>

  <template v-if="expanded">
    <n-spin :show="loading">
      <div v-if="stats" class="stats-summary">
        <div class="stat-item"><span class="stat-num">{{ stats.total_tasks }}</span><span class="stat-label">总任务</span></div>
        <div class="stat-item"><span class="stat-num ok">{{ stats.completed }}</span><span class="stat-label">成功</span></div>
        <div class="stat-item"><span class="stat-num bad">{{ stats.failed }}</span><span class="stat-label">失败</span></div>
        <div class="stat-item"><span class="stat-num">{{ stats.success_rate }}%</span><span class="stat-label">成功率</span></div>
        <div class="stat-item"><span class="stat-num">{{ stats.total_found }}</span><span class="stat-label">发现</span></div>
        <div class="stat-item"><span class="stat-num ok">{{ stats.total_added }}</span><span class="stat-label">入库</span></div>
      </div>
      <div v-else-if="!loading" class="stats-empty">暂无统计数据</div>

      <div v-if="stats && stats.by_platform.length" class="stats-charts">
        <div class="chart-box"><div class="chart-title">各平台采集</div><div ref="platformRef" class="chart" /></div>
        <div class="chart-box"><div class="chart-title">每日趋势</div><div ref="dayRef" class="chart" /></div>
      </div>
      <div v-else-if="stats && !loading" class="stats-empty">近 30 天暂无采集任务</div>
    </n-spin>
  </template>
</n-card>
</template>

<style scoped>
.stats-summary{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
.stat-item{display:flex;flex-direction:column;align-items:center;min-width:84px;padding:10px 14px;border-radius:8px;background:#f6f6f4}
.stat-num{font-size:20px;font-weight:bold;color:#333}
.stat-num.ok{color:#18a058}
.stat-num.bad{color:#d03050}
.stat-label{font-size:12px;color:#999;margin-top:2px}
.stats-charts{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.chart-box{border:1px solid #eee;border-radius:8px;padding:8px}
.chart-title{font-size:12px;color:#666;text-align:center}
.chart{height:240px}
.stats-empty{text-align:center;color:#999;padding:24px 0;font-size:13px}
@media (max-width: 900px){.stats-charts{grid-template-columns:1fr}}
</style>
