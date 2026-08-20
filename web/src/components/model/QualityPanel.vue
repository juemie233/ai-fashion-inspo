<script setup lang="ts">
/** 分析质量仪表盘面板：总览、问题素材、趋势/模型对比/错误分布（echarts）、失败素材直达列表。 */

import { h, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Tag, type TableColumnData } from '@arco-design/web-vue'
import type { EChartsOption } from 'echarts'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'
import { formatDate } from '@/utils/format'
import ArcoChart from '@/components/chart/ArcoChart.vue'
import StatCardGrid from '@/components/common/StatCardGrid.vue'

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
  model_comparison: Array<{
    model_name: string
    total: number
    success: number
    success_rate: number
  }>
  error_distribution: Array<{ category: string; count: number }>
  failed_items: FailedItem[]
}

const qualityData = ref<QualityDashboard | null>(null)
const qualityLoading = ref(false)

/** 平均耗时展示：拆分为数字 + 单位（a-statistic 的 value 仅接受数字，无数据时显示占位符） */
const avgTime = computed(() => {
  const ms = qualityData.value?.overview.avg_time_ms
  if (ms == null) return { value: undefined, precision: 0, suffix: '' }
  if (ms < 1000) return { value: ms, precision: 0, suffix: 'ms' }
  return { value: Number((ms / 1000).toFixed(1)), precision: 1, suffix: 's' }
})

async function loadQuality() {
  qualityLoading.value = true
  try {
    const { data } = await apiClient.get<QualityDashboard>('/ai/quality-dashboard')
    qualityData.value = data
  } catch {
    /* 静默 */
  } finally {
    qualityLoading.value = false
  }
}

/** 每日趋势：总量与成功量折线 */
const trendOption = computed<EChartsOption | null>(() => {
  const rows = qualityData.value?.daily_trends ?? []
  if (rows.length === 0) return null
  return {
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((r) => r.day.slice(5)), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      {
        name: '总量',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: rows.map((r) => r.total),
        itemStyle: { color: '#3b82f6' },
        areaStyle: { opacity: 0.08 },
      },
      {
        name: '成功',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: rows.map((r) => r.success),
        itemStyle: { color: '#22c55e' },
      },
    ],
  }
})

/** 按模型成功率对比：成功/失败堆叠柱状图 */
const modelOption = computed<EChartsOption | null>(() => {
  const rows = qualityData.value?.model_comparison ?? []
  if (rows.length === 0) return null
  return {
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.model_name),
      axisLabel: { fontSize: 10, rotate: 20 },
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      {
        name: '成功',
        type: 'bar',
        stack: 'total',
        data: rows.map((r) => r.success),
        itemStyle: { color: '#22c55e' },
        barMaxWidth: 28,
      },
      {
        name: '失败',
        type: 'bar',
        stack: 'total',
        data: rows.map((r) => r.total - r.success),
        itemStyle: { color: '#d03050' },
        barMaxWidth: 28,
      },
    ],
  }
})

/** 错误原因分布：环形图 */
const errorOption = computed<EChartsOption | null>(() => {
  const rows = qualityData.value?.error_distribution ?? []
  if (rows.length === 0) return null
  return {
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
  }
})

onMounted(loadQuality)

/** 失败素材表格列定义 */
const failedColumns: TableColumnData[] = [
  {
    title: '预览',
    dataIndex: 'thumb',
    width: 60,
    render: ({ record }) => {
      const row = record as FailedItem
      return row.thumbnail_path
        ? h('img', {
            src: getFileUrl(row.thumbnail_path),
            style: 'width:40px;height:56px;object-fit:cover;border-radius:4px',
          })
        : '-'
    },
  },
  {
    title: '素材 ID',
    dataIndex: 'id',
    width: 200,
    render: ({ record }) =>
      h(
        'span',
        { style: 'font-size:12px;word-break:break-all' },
        (record as FailedItem).inspiration_id,
      ),
  },
  {
    title: '模型',
    dataIndex: 'model',
    width: 150,
    render: ({ record }) => h(Tag, { size: 'small' }, () => (record as FailedItem).model_name),
  },
  {
    title: '失败原因',
    dataIndex: 'error',
    render: ({ record }) => {
      const row = record as FailedItem
      return h(
        'span',
        {
          title: row.error,
          style:
            'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:320px',
        },
        row.error || '-',
      )
    },
  },
  {
    title: '时间',
    dataIndex: 'time',
    width: 150,
    render: ({ record }) => formatDate((record as FailedItem).created_at),
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 80,
    render: ({ record }) => {
      const row = record as FailedItem
      return h(
        Button,
        { size: 'mini', onClick: () => router.push(`/detail/${row.inspiration_id}`) },
        () => '查看',
      )
    },
  },
]
</script>

<template>
  <a-spin :loading="qualityLoading" style="display: block">
    <template v-if="qualityData">
      <!-- 总览卡片 -->
      <StatCardGrid
        :items="[
          { title: '素材总数', value: qualityData.overview.total_inspirations },
          { title: '已分析', value: qualityData.overview.analyzed_count },
          { title: '覆盖率', value: qualityData.overview.coverage_percent, suffix: '%' },
          { title: '平均标签', value: qualityData.overview.avg_tags_per_image },
          {
            title: '平均耗时',
            value: avgTime.value,
            precision: avgTime.precision,
            placeholder: '-',
            suffix: avgTime.suffix,
          },
        ]"
      />

      <!-- 问题素材 -->
      <a-row :gutter="[12, 12]" style="margin-bottom: 16px">
        <a-col :flex="1">
          <a-card
            size="small"
            :style="{
              borderColor: qualityData.problem_items.multi_fail_count > 0 ? '#d03050' : '#e5e7eb',
            }"
          >
            <a-statistic
              title="🔴 多次失败 (≥3次)"
              :value="qualityData.problem_items.multi_fail_count"
            />
          </a-card>
        </a-col>
        <a-col :flex="1">
          <a-card
            size="small"
            :style="{
              borderColor: qualityData.problem_items.zero_tag_count > 0 ? '#f0a020' : '#e5e7eb',
            }"
          >
            <a-statistic title="🟡 零标签输出" :value="qualityData.problem_items.zero_tag_count" />
          </a-card>
        </a-col>
      </a-row>

      <!-- 每日趋势 -->
      <a-card title="每日分析趋势（最近 30 天）" size="small" style="margin-bottom: 16px">
        <ArcoChart :option="trendOption" :height="220" empty-text="最近 30 天无分析记录" />
      </a-card>

      <!-- 模型成功率对比 + 错误原因分布 -->
      <a-row :gutter="[12, 12]" style="margin-bottom: 16px">
        <a-col :flex="1">
          <a-card title="按模型成功率对比" size="small">
            <ArcoChart :option="modelOption" :height="220" empty-text="暂无分析数据" />
          </a-card>
        </a-col>
        <a-col :flex="1">
          <a-card title="错误原因分布" size="small">
            <ArcoChart :option="errorOption" :height="220" empty-text="暂无失败记录" />
          </a-card>
        </a-col>
      </a-row>

      <!-- 失败素材直达列表 -->
      <a-card title="最近失败素材" size="small">
        <a-table
          v-if="qualityData.failed_items.length"
          :columns="failedColumns"
          :data="qualityData.failed_items"
          :bordered="false"
          size="small"
          :max-height="360"
          :pagination="false"
        />
        <a-empty v-else description="暂无失败素材" />
      </a-card>
    </template>
    <a-empty v-else-if="!qualityLoading" description="点击加载质量数据">
      <a-button size="small" @click="loadQuality">加载</a-button>
    </a-empty>
  </a-spin>
</template>

<style scoped>
.chart {
  height: 220px;
}
</style>
