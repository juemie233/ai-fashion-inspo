<script setup lang="ts">
/** 分析质量仪表盘面板。 */

import { ref, onMounted } from 'vue'
import apiClient from '@/api/client'
import { formatMs } from '@/utils/format'

interface QualityDashboard {
  daily_trends: Array<{ day: string; total: number; success: number }>
  overview: Record<string, any>
  problem_items: Record<string, number>
}
const qualityData = ref<QualityDashboard | null>(null)
const qualityLoading = ref(false)

async function loadQuality() {
  qualityLoading.value = true
  try {
    const { data } = await apiClient.get<QualityDashboard>('/ai/quality-dashboard')
    qualityData.value = data
  } catch { /* 静默 */ }
  finally { qualityLoading.value = false }
}

onMounted(loadQuality)
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
      <n-card title="每日分析趋势（最近 30 天）" size="small">
        <div v-if="qualityData.daily_trends.length > 0" class="trend-chart">
          <div v-for="d in qualityData.daily_trends" :key="d.day" class="trend-bar-item">
            <div class="trend-bar-wrap">
              <div class="trend-bar" :style="{ height: Math.max(d.total / Math.max(...qualityData.daily_trends.map(x=>x.total)) * 100, 2) + '%', background: d.success === d.total ? '#22c55e' : '#3b82f6' }" />
            </div>
            <div class="trend-bar-label">{{ d.day.slice(5) }}</div>
            <div class="trend-bar-value">{{ d.total }}</div>
          </div>
        </div>
        <n-empty v-else description="最近 30 天无分析记录" size="small" />
      </n-card>
    </template>
    <n-empty v-else-if="!qualityLoading" description="点击加载质量数据" size="small">
      <template #extra><n-button size="small" @click="loadQuality">加载</n-button></template>
    </n-empty>
  </n-spin>
</template>

<style scoped>
.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 160px;
  padding: 8px 0;
}
.trend-bar-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 0;
}
.trend-bar-wrap {
  width: 100%;
  height: 120px;
  display: flex;
  align-items: flex-end;
}
.trend-bar {
  width: 100%;
  border-radius: 2px 2px 0 0;
  min-height: 2px;
  transition: height .2s;
}
.trend-bar-label {
  font-size: 9px;
  color: #999;
  margin-top: 4px;
  writing-mode: vertical-rl;
}
.trend-bar-value {
  font-size: 9px;
  color: #666;
  font-weight: 600;
}
</style>
