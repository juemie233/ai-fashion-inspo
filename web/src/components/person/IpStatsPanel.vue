<script setup lang="ts">
/** 博主 IP 属地统计卡片：ECharts 横向柱状图（ArcoChart 封装），仅穿搭博主页展示。 */

import { computed, onMounted, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { bloggersApi, type PersonIpStats } from '@/api/persons'
import ArcoChart from '@/components/chart/ArcoChart.vue'

const ipStats = ref<PersonIpStats | null>(null)

/** 图表配置：y 轴地区（最多在上），x 轴人数；无数据返回 null（ArcoChart 显示空态） */
const chartOption = computed<EChartsOption | null>(() => {
  const items = ipStats.value?.items ?? []
  if (items.length === 0) return null
  const rows = [...items].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: rows.map((i) => i.ip_location) },
    series: [
      {
        type: 'bar',
        data: rows.map((i) => i.count),
        barMaxWidth: 18,
        itemStyle: { color: '#18a058', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right' },
      },
    ],
  }
})

/** 加载博主 IP 属地统计 */
async function loadIpStats() {
  try {
    ipStats.value = await bloggersApi.fetchIpStats(30)
  } catch {
    // 统计加载失败不阻塞列表
  }
}

onMounted(loadIpStats)
</script>

<template>
  <a-card size="small" class="ipstats-card" title="博主 IP 属地统计">
    <template #extra>
      <a-typography-text type="secondary" style="font-size: 12px">
        共 {{ ipStats?.total ?? 0 }} 位博主
      </a-typography-text>
    </template>
    <ArcoChart
      :option="chartOption"
      :height="320"
      empty-text="暂无 IP 属地数据（可从 CSV 导入或编辑博主补充）"
    />
  </a-card>
</template>

<style scoped>
.ipstats-card {
  margin-bottom: 12px;
}
</style>
