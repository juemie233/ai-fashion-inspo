<script setup lang="ts">
/** 博主 IP 属地统计卡片：ECharts 横向柱状图（ArcoChart 封装），仅穿搭博主页展示。
 *
 * 图表配置与高度在 `utils/ipStatsChart.ts`（纯函数，便于用真实数据断言「标签不会被
 * ECharts 自动藏掉」）；本组件只负责拉数据与组装。
 */

import { computed, onMounted, ref } from 'vue'
import { bloggersApi, type PersonIpStats } from '@/api/persons'
import { buildIpStatsChartOption, ipStatsChartHeight } from '@/utils/ipStatsChart'
import ArcoChart from '@/components/chart/ArcoChart.vue'

const ipStats = ref<PersonIpStats | null>(null)

const items = computed(() => ipStats.value?.items ?? [])
/** 图表配置：y 轴地区（人数最多在上）、x 轴人数；无数据返回 null（ArcoChart 显示空态） */
const chartOption = computed(() => buildIpStatsChartOption(items.value))
/** 容器高度随类目数增长：高度不够时 ECharts 会隔一个藏一个类目名 */
const chartHeight = computed(() => ipStatsChartHeight(items.value.length))

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
      :height="chartHeight"
      empty-text="暂无 IP 属地数据（可从 CSV 导入或编辑博主补充）"
    />
  </a-card>
</template>

<style scoped>
.ipstats-card {
  margin-bottom: 12px;
}
</style>
