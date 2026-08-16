<script setup lang="ts">
/** 素材新增趋势图：按天统计近 N 天新增素材数量，柱状图展示。 */

import { computed, onMounted, ref, watch } from 'vue'
import { fetchInspirationTrend, type TrendPoint } from '@/api/admin'

const days = ref(30)
const trend = ref<TrendPoint[]>([])
const loading = ref(false)

const maxCount = computed(() => Math.max(1, ...trend.value.map((p) => p.count)))
const totalCount = computed(() => trend.value.reduce((sum, p) => sum + p.count, 0))

/** 柱高（像素）：按最大值归一化，最小值保底可见 */
function barHeight(count: number): string {
  return `${Math.max(4, Math.round((count / maxCount.value) * 140))}px`
}

/** X 轴标签步长：标签过多时抽稀显示 */
const labelStep = computed(() => {
  const n = trend.value.length
  if (n <= 14) return 1
  if (n <= 45) return 7
  return 14
})

async function load() {
  loading.value = true
  try {
    trend.value = await fetchInspirationTrend(days.value)
  } catch {
    trend.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(days, load)
</script>

<template>
  <n-card title="素材新增趋势" size="small">
    <template #header-extra>
      <n-radio-group v-model:value="days" size="tiny">
        <n-radio-button :value="7">近 7 天</n-radio-button>
        <n-radio-button :value="30">近 30 天</n-radio-button>
        <n-radio-button :value="90">近 90 天</n-radio-button>
      </n-radio-group>
    </template>

    <div v-if="loading" class="trend-loading">
      <n-spin size="small" />
    </div>

    <div v-else-if="trend.length === 0" class="trend-empty">
      近 {{ days }} 天暂无新增素材
    </div>

    <div v-else>
      <div class="trend-summary">
        近 {{ days }} 天共新增 <strong>{{ totalCount }}</strong> 条，单日最高 <strong>{{ maxCount }}</strong> 条
      </div>
      <div class="trend-chart">
        <div
          v-for="(p, i) in trend"
          :key="p.day"
          class="bar-col"
          :title="`${p.day}：${p.count} 条`"
        >
          <div class="bar-value">{{ p.count }}</div>
          <div class="bar" :style="{ height: barHeight(p.count) }" />
          <span v-if="i % labelStep === 0 || i === trend.length - 1" class="bar-label">
            {{ p.day.slice(5) }}
          </span>
        </div>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.trend-loading,
.trend-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  color: #999;
  font-size: 13px;
}
.trend-summary {
  font-size: 12px;
  color: #666;
  margin-bottom: 12px;
}
.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 180px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.bar-col {
  flex: 1;
  min-width: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
}
.bar-value {
  font-size: 10px;
  color: #999;
  line-height: 1;
  margin-bottom: 2px;
}
.bar {
  width: 100%;
  max-width: 22px;
  background: linear-gradient(180deg, #5b8def, #2d6be6);
  border-radius: 3px 3px 0 0;
  min-height: 4px;
}
.bar-label {
  font-size: 10px;
  color: #bbb;
  margin-top: 4px;
  white-space: nowrap;
  transform: rotate(-30deg);
  transform-origin: top left;
}
</style>
