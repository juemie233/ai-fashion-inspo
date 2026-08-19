<script setup lang="ts">
/** 采集统计看板：总量概览 + 平台分布柱状图 + 每日趋势折线图（可折叠卡片）。 */

import { computed, nextTick, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { useScraperStats } from '@/composables/useScraperStats'
import { PLATFORM_LABELS } from '@/composables/useScraperTasks'
import ArcoChart from '@/components/chart/ArcoChart.vue'

const { stats, loading, loadStats } = useScraperStats()

/** 是否展开看板 */
const expanded = ref(false)

/** 平台分布柱状图配置 */
const platformOption = computed<EChartsOption | null>(() => {
  const rows = stats.value?.by_platform ?? []
  if (rows.length === 0) return null
  const names = rows.map((r) => PLATFORM_LABELS[r.platform] || r.platform)
  return {
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: names, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      {
        name: '任务数',
        type: 'bar',
        data: rows.map((r) => r.tasks),
        itemStyle: { color: '#2a78d6' },
        barMaxWidth: 32,
      },
      {
        name: '入库素材',
        type: 'bar',
        data: rows.map((r) => r.added),
        itemStyle: { color: '#1baf7a' },
        barMaxWidth: 32,
      },
    ],
  }
})

/** 每日趋势折线图配置 */
const dayOption = computed<EChartsOption | null>(() => {
  const rows = stats.value?.by_day ?? []
  if (rows.length === 0) return null
  return {
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.date.slice(5)),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      {
        name: '入库素材',
        type: 'line',
        smooth: true,
        data: rows.map((r) => r.added),
        itemStyle: { color: '#1baf7a' },
        areaStyle: { opacity: 0.08 },
      },
      {
        name: '失败任务',
        type: 'line',
        smooth: true,
        data: rows.map((r) => r.failed),
        itemStyle: { color: '#d03050' },
      },
    ],
  }
})

/** 展开时加载数据（ArcoChart 由 option computed 驱动自动渲染） */
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) {
    nextTick(() => setTimeout(() => loadStats(30), 30))
  }
}

async function refresh() {
  await loadStats(30)
}
</script>

<template>
  <a-card size="small" style="margin-bottom: 16px">
    <template #title>
      <div
        style="
          display: flex;
          align-items: center;
          justify-content: space-between;
          cursor: pointer;
          user-select: none;
        "
        @click="toggle"
      >
        <span>📊 采集统计（近 30 天）{{ expanded ? '▼' : '▶' }}</span>
        <a-button v-if="expanded" size="mini" type="text" @click.stop="refresh">刷新</a-button>
      </div>
    </template>

    <template v-if="expanded">
      <a-spin :loading="loading">
        <div v-if="stats" class="stats-summary">
          <div class="stat-item">
            <span class="stat-num">{{ stats.total_tasks }}</span
            ><span class="stat-label">总任务</span>
          </div>
          <div class="stat-item">
            <span class="stat-num ok">{{ stats.completed }}</span
            ><span class="stat-label">成功</span>
          </div>
          <div class="stat-item">
            <span class="stat-num bad">{{ stats.failed }}</span
            ><span class="stat-label">失败</span>
          </div>
          <div class="stat-item">
            <span class="stat-num">{{ stats.success_rate }}%</span
            ><span class="stat-label">成功率</span>
          </div>
          <div class="stat-item">
            <span class="stat-num">{{ stats.total_found }}</span
            ><span class="stat-label">发现</span>
          </div>
          <div class="stat-item">
            <span class="stat-num ok">{{ stats.total_added }}</span
            ><span class="stat-label">入库</span>
          </div>
        </div>
        <div v-else-if="!loading" class="stats-empty">暂无统计数据</div>

        <div v-if="stats && stats.by_platform.length" class="stats-charts">
          <div class="chart-box">
            <div class="chart-title">各平台采集</div>
            <ArcoChart :option="platformOption" :height="240" empty-text="暂无数据" />
          </div>
          <div class="chart-box">
            <div class="chart-title">每日趋势</div>
            <ArcoChart :option="dayOption" :height="240" empty-text="暂无数据" />
          </div>
        </div>
        <div v-else-if="stats && !loading" class="stats-empty">近 30 天暂无采集任务</div>
      </a-spin>
    </template>
  </a-card>
</template>

<style scoped>
.stats-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 84px;
  padding: 10px 14px;
  border-radius: 8px;
  background: #f6f6f4;
}
.stat-num {
  font-size: 20px;
  font-weight: bold;
  color: #333;
}
.stat-num.ok {
  color: #18a058;
}
.stat-num.bad {
  color: #d03050;
}
.stat-label {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}
.stats-charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.chart-box {
  border: 1px solid #eee;
  border-radius: 8px;
  padding: 8px;
}
.chart-title {
  font-size: 12px;
  color: #666;
  text-align: center;
}
.stats-empty {
  text-align: center;
  color: #999;
  padding: 24px 0;
  font-size: 13px;
}
@media (max-width: 900px) {
  .stats-charts {
    grid-template-columns: 1fr;
  }
}
</style>
