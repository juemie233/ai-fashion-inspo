<script setup lang="ts">
/** 分析质量仪表盘面板：总览、问题素材、趋势/模型对比/错误分布（echarts）、失败素材直达列表。 */

import { h, ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag } from 'naive-ui'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'
import { formatMs, formatDate } from '@/utils/format'

echarts.use([BarChart, LineChart, PieChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const router = useRouter()

interface FailedItem {
  inspiration_id: string
  model_name: string
  error: string
  created_at: string
  thumbnail_path: string | null
}

interface QualityDashboard {
  daily_trends: Array<{ day: string; total: number; success: number }>
  overview: Record<string, any>
  problem_items: Record<string, number>
  model_comparison: Array<{ model_name: string; total: number; success: number; success_rate: number }>
  error_distribution: Array<{ category: string; count: number }>
  failed_items: FailedItem[]
}

const qualityData = ref<QualityDashboard | null>(null)
const qualityLoading = ref(false)

const trendRef = ref<HTMLDivElement | null>(null)
const modelRef = ref<HTMLDivElement | null>(null)
const errorRef = ref<HTMLDivElement | null>(null)
let trendChart: echarts.ECharts | null = null
let modelChart: echarts.ECharts | null = null
let errorChart: echarts.ECharts | null = null

async function loadQuality() {
  qualityLoading.value = true
  try {
    const { data } = await apiClient.get<QualityDashboard>('/ai/quality-dashboard')
    qualityData.value = data
    nextTick(renderCharts)
  } catch { /* 静默 */ }
  finally { qualityLoading.value = false }
}

watch(qualityData, () => nextTick(renderCharts), { deep: true })

/** 渲染全部图表 */
function renderCharts() {
  renderTrend()
  renderModelComparison()
  renderErrorDistribution()
}

/** 每日趋势：总量与成功量折线 */
function renderTrend() {
  if (!qualityData.value || !trendRef.value) return
  if (!trendChart || trendChart.isDisposed()) trendChart = echarts.init(trendRef.value)
  const rows = qualityData.value.daily_trends
  trendChart.setOption({
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((r) => r.day.slice(5)), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      { name: '总量', type: 'line', smooth: true, showSymbol: false, data: rows.map((r) => r.total), itemStyle: { color: '#3b82f6' }, areaStyle: { opacity: 0.08 } },
      { name: '成功', type: 'line', smooth: true, showSymbol: false, data: rows.map((r) => r.success), itemStyle: { color: '#22c55e' } },
    ],
  })
}

/** 按模型成功率对比：成功/失败堆叠柱状图 */
function renderModelComparison() {
  if (!qualityData.value || !modelRef.value) return
  if (!modelChart || modelChart.isDisposed()) modelChart = echarts.init(modelRef.value)
  const rows = qualityData.value.model_comparison
  modelChart.setOption({
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((r) => r.model_name), axisLabel: { fontSize: 10, rotate: 20 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      { name: '成功', type: 'bar', stack: 'total', data: rows.map((r) => r.success), itemStyle: { color: '#22c55e' }, barMaxWidth: 28 },
      { name: '失败', type: 'bar', stack: 'total', data: rows.map((r) => r.total - r.success), itemStyle: { color: '#d03050' }, barMaxWidth: 28 },
    ],
  })
}

/** 错误原因分布：环形图 */
function renderErrorDistribution() {
  if (!qualityData.value || !errorRef.value) return
  if (!errorChart || errorChart.isDisposed()) errorChart = echarts.init(errorRef.value)
  const rows = qualityData.value.error_distribution
  errorChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { top: 0, left: 'center', textStyle: { fontSize: 11 } },
    series: [
      {
        name: '失败原因',
        type: 'pie',
        radius: ['40%', '65%'],
        avoidLabelOverlap: true,
        label: { show: false },
        data: rows.map((r) => ({ name: r.category, value: r.count })),
      },
    ],
  })
}

function handleResize() {
  trendChart?.resize()
  modelChart?.resize()
  errorChart?.resize()
}

onMounted(() => {
  loadQuality()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose(); trendChart = null
  modelChart?.dispose(); modelChart = null
  errorChart?.dispose(); errorChart = null
})
</script>

<template>
  <n-spin :show="qualityLoading">
    <template v-if="qualityData">
      <!-- 总览卡片 -->
      <n-grid :cols="5" :x-gap="12" style="margin-bottom:16px">
        <n-gi><n-card size="small"><n-statistic label="素材总数" :value="qualityData.overview.total_inspirations" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="已分析" :value="qualityData.overview.analyzed_count" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="覆盖率" :value="`${qualityData.overview.coverage_percent}%`" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="平均标签" :value="qualityData.overview.avg_tags_per_image" /></n-card></n-gi>
        <n-gi><n-card size="small"><n-statistic label="平均耗时" :value="formatMs(qualityData.overview.avg_time_ms)" /></n-card></n-gi>
      </n-grid>

      <!-- 问题素材 -->
      <n-grid :cols="2" :x-gap="12" style="margin-bottom:16px">
        <n-gi>
          <n-card size="small" :bordered="true" :style="{ borderColor: (qualityData.problem_items.multi_fail_count > 0 ? '#d03050' : '#e5e7eb') }">
            <n-statistic label="🔴 多次失败 (≥3次)" :value="qualityData.problem_items.multi_fail_count" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card size="small" :bordered="true" :style="{ borderColor: (qualityData.problem_items.zero_tag_count > 0 ? '#f0a020' : '#e5e7eb') }">
            <n-statistic label="🟡 零标签输出" :value="qualityData.problem_items.zero_tag_count" />
          </n-card>
        </n-gi>
      </n-grid>

      <!-- 每日趋势 -->
      <n-card title="每日分析趋势（最近 30 天）" size="small" style="margin-bottom:16px">
        <div v-if="qualityData.daily_trends.length" ref="trendRef" class="chart" />
        <n-empty v-else description="最近 30 天无分析记录" size="small" />
      </n-card>

      <!-- 模型成功率对比 + 错误原因分布 -->
      <n-grid :cols="2" :x-gap="12" style="margin-bottom:16px">
        <n-gi>
          <n-card title="按模型成功率对比" size="small">
            <div v-if="qualityData.model_comparison.length" ref="modelRef" class="chart" />
            <n-empty v-else description="暂无分析数据" size="small" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card title="错误原因分布" size="small">
            <div v-if="qualityData.error_distribution.length" ref="errorRef" class="chart" />
            <n-empty v-else description="暂无失败记录" size="small" />
          </n-card>
        </n-gi>
      </n-grid>

      <!-- 失败素材直达列表 -->
      <n-card title="最近失败素材" size="small">
        <n-data-table
          v-if="qualityData.failed_items.length"
          :columns="[
            { title: '预览', key: 'thumb', width: 60, render: (row: FailedItem) => row.thumbnail_path ? h('img', { src: getFileUrl(row.thumbnail_path), style: 'width:40px;height:56px;object-fit:cover;border-radius:4px' }) : '-' },
            { title: '素材 ID', key: 'id', width: 200, render: (row: FailedItem) => h('span', { style: 'font-size:12px;word-break:break-all' }, row.inspiration_id) },
            { title: '模型', key: 'model', width: 150, render: (row: FailedItem) => h(NTag, { size: 'tiny' }, row.model_name) },
            { title: '失败原因', key: 'error', render: (row: FailedItem) => h('span', { title: row.error, style: 'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:320px' }, row.error || '-') },
            { title: '时间', key: 'time', width: 150, render: (row: FailedItem) => formatDate(row.created_at) },
            { title: '操作', key: 'actions', width: 80, render: (row: FailedItem) => h(NButton, { size: 'tiny', onClick: () => router.push(`/detail/${row.inspiration_id}`) }, '查看') },
          ]"
          :data="qualityData.failed_items"
          :bordered="false"
          size="small"
          :max-height="360"
        />
        <n-empty v-else description="暂无失败素材" size="small" />
      </n-card>
    </template>
    <n-empty v-else-if="!qualityLoading" description="点击加载质量数据" size="small">
      <template #extra><n-button size="small" @click="loadQuality">加载</n-button></template>
    </n-empty>
  </n-spin>
</template>

<style scoped>
.chart { height: 220px; }
</style>
