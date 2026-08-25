<script setup lang="ts">
/** 效果分析面板：升降榜 / 组合排行 / 覆盖度 / 来源分布 四个子视图。 */

import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { EChartsOption } from 'echarts'
import { getApiErrorMessage } from '@/utils/apiError'
import { fetchCombinations, fetchCoverage, fetchSourceDist, fetchTrending } from '@/api/tagAdvanced'
import { CATEGORY_LABELS, SOURCE_LABELS } from '@/constants/tag'
import ArcoChart from '@/components/chart/ArcoChart.vue'
import type { CoverageStats, SourceDist, TrendingItem } from '@/types/tagAdvanced'

type EffectTab = 'trending' | 'combinations' | 'coverage' | 'source'

const activeTab = ref<EffectTab>('trending')
const days = ref(30)

// ── 数据 ──
const rising = ref<TrendingItem[]>([])
const falling = ref<TrendingItem[]>([])
const combinations = ref<Array<{ tags: [string, string]; count: number }>>([])
const coverage = ref<CoverageStats | null>(null)
const sourceDist = ref<SourceDist | null>(null)
const loading = ref(false)

async function loadAll() {
  loading.value = true
  try {
    const [t, c, cv, s] = await Promise.all([
      fetchTrending(days.value, 20),
      fetchCombinations(20, 2),
      fetchCoverage(),
      fetchSourceDist(),
    ])
    rising.value = t.rising
    falling.value = t.falling
    combinations.value = c.pairs
    coverage.value = cv
    sourceDist.value = s
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载效果分析失败'))
  } finally {
    loading.value = false
  }
}

function reloadTrending() {
  fetchTrending(days.value, 20)
    .then((t) => {
      rising.value = t.rising
      falling.value = t.falling
    })
    .catch((e) => Message.error(getApiErrorMessage(e, '加载升降榜失败')))
}

// ── 图表 option ──
const MUTED_INK = '#898781'
const SERIES_BLUE = '#2a78d6'

const combinationOption = computed<EChartsOption | null>(() => {
  const rows = combinations.value.slice(0, 12)
  if (!rows.length) return null
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.tags.join(' × ')),
      axisLabel: { color: MUTED_INK, fontSize: 10, rotate: 20 },
    },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: MUTED_INK } },
    tooltip: { trigger: 'axis' },
    series: [
      {
        type: 'bar',
        data: rows.map((r) => r.count),
        itemStyle: { color: SERIES_BLUE, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 28,
      },
    ],
  } as EChartsOption
})

const coverageOption = computed<EChartsOption | null>(() => {
  const by = coverage.value?.by_category ?? {}
  const entries = Object.entries(by).sort((a, b) => b[1] - a[1])
  if (!entries.length) return null
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: entries.map(([k]) => CATEGORY_LABELS[k] ?? k),
      axisLabel: { color: MUTED_INK, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      max: 1,
      axisLabel: { color: MUTED_INK, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
    },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: unknown) => `${((v as number) * 100).toFixed(1)}%`,
    },
    series: [
      {
        type: 'bar',
        data: entries.map(([, v]) => v),
        itemStyle: { color: '#1baf7a', borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 28,
      },
    ],
  } as EChartsOption
})

const sourceOption = computed<EChartsOption | null>(() => {
  const by = sourceDist.value?.by_source ?? {}
  const entries = Object.entries(by)
  if (!entries.length) return null
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: MUTED_INK } },
    series: [
      {
        type: 'pie',
        radius: ['40%', '65%'],
        center: ['50%', '45%'],
        label: { color: '#52514e', fontSize: 11 },
        data: entries.map(([k, v]) => ({
          name: SOURCE_LABELS[k] ?? k,
          value: v.usage_total,
        })),
      },
    ],
  } as EChartsOption
})

// ── 表格格式化 ──
function deltaTag(item: TrendingItem): { text: string; color: string } {
  if (item.delta > 0) return { text: `↑${item.delta}`, color: '#e34948' }
  if (item.delta < 0) return { text: `↓${Math.abs(item.delta)}`, color: '#1baf7a' }
  return { text: '—', color: '#9ca3af' }
}

onMounted(() => {
  loadAll()
})
</script>

<template>
  <div class="effect-panel">
    <!-- 子视图切换 -->
    <div class="effect-tabs">
      <a-radio-group v-model="activeTab" type="button" size="small">
        <a-radio value="trending">热度升降榜</a-radio>
        <a-radio value="combinations">标签组合</a-radio>
        <a-radio value="coverage">覆盖度</a-radio>
        <a-radio value="source">来源分布</a-radio>
      </a-radio-group>
      <a-button size="small" :loading="loading" @click="loadAll">刷新</a-button>
    </div>

    <a-spin :loading="loading" class="effect-spin">
      <!-- 升降榜 -->
      <div v-if="activeTab === 'trending'" class="effect-body">
        <div class="trending-bar">
          <span>对比窗口（天）</span>
          <a-input-number v-model="days" :min="7" :max="365" :step="7" style="width: 100px" />
          <a-button size="small" @click="reloadTrending">应用</a-button>
        </div>
        <div class="trending-cols">
          <div class="trend-col">
            <h4 class="col-title rise">上升最快</h4>
            <a-table :data="rising" :pagination="false" size="small">
              <template #columns>
                <a-table-column title="标签" data-index="name" />
                <a-table-column title="本期" data-index="current" :width="70" />
                <a-table-column title="上期" data-index="previous" :width="70" />
                <a-table-column title="变化" :width="80">
                  <template #cell="{ record }">
                    <span :style="{ color: deltaTag(record).color, fontWeight: 600 }">
                      {{ deltaTag(record).text }}
                    </span>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
          <div class="trend-col">
            <h4 class="col-title fall">下降最快</h4>
            <a-table :data="falling" :pagination="false" size="small">
              <template #columns>
                <a-table-column title="标签" data-index="name" />
                <a-table-column title="本期" data-index="current" :width="70" />
                <a-table-column title="上期" data-index="previous" :width="70" />
                <a-table-column title="变化" :width="80">
                  <template #cell="{ record }">
                    <span :style="{ color: deltaTag(record).color, fontWeight: 600 }">
                      {{ deltaTag(record).text }}
                    </span>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
        </div>
      </div>

      <!-- 组合排行 -->
      <div v-if="activeTab === 'combinations'" class="effect-body">
        <ArcoChart :option="combinationOption" :height="320" empty-text="暂无组合数据" />
        <div class="combo-list">
          <div v-for="(c, i) in combinations" :key="i" class="combo-item">
            <span class="combo-tags">{{ c.tags.join(' × ') }}</span>
            <a-tag color="arcoblue">{{ c.count }} 次</a-tag>
          </div>
        </div>
      </div>

      <!-- 覆盖度 -->
      <div v-if="activeTab === 'coverage'" class="effect-body">
        <div class="cov-cards">
          <div class="cov-card">
            <div class="cov-label">素材总量</div>
            <div class="cov-value">{{ coverage?.inspiration_total ?? '--' }}</div>
          </div>
          <div class="cov-card">
            <div class="cov-label">带标签素材</div>
            <div class="cov-value">{{ coverage?.with_tags ?? '--' }}</div>
          </div>
          <div class="cov-card">
            <div class="cov-label">标签覆盖率</div>
            <div class="cov-value">
              {{ coverage ? `${(coverage.tagged_ratio * 100).toFixed(1)}%` : '--' }}
            </div>
          </div>
          <div class="cov-card">
            <div class="cov-label">单素材平均标签数</div>
            <div class="cov-value">{{ coverage?.avg_tags_per_inspiration ?? '--' }}</div>
          </div>
        </div>
        <h4 class="section-title">按类别覆盖率</h4>
        <ArcoChart :option="coverageOption" :height="260" empty-text="暂无数据" />
      </div>

      <!-- 来源分布 -->
      <div v-if="activeTab === 'source'" class="effect-body">
        <div class="source-grid">
          <div class="source-chart">
            <h4 class="section-title">来源使用分布</h4>
            <ArcoChart :option="sourceOption" :height="280" empty-text="暂无数据" />
          </div>
          <div class="source-table">
            <h4 class="section-title">来源统计</h4>
            <a-table
              :data="
                Object.entries(sourceDist?.by_source ?? {}).map(([k, v]) => ({ source: k, ...v }))
              "
              :pagination="false"
              size="small"
            >
              <template #columns>
                <a-table-column title="来源" data-index="source">
                  <template #cell="{ record }">{{
                    SOURCE_LABELS[record.source] ?? record.source
                  }}</template>
                </a-table-column>
                <a-table-column title="标签数" data-index="tag_count" :width="80" />
                <a-table-column title="总使用" data-index="usage_total" :width="90" />
                <a-table-column title="平均使用" data-index="avg_usage" :width="90" />
              </template>
            </a-table>
            <h4 class="section-title">低效 AI 标签（使用 ≤1 次）</h4>
            <a-table :data="sourceDist?.top_low_quality ?? []" :pagination="false" size="small">
              <template #columns>
                <a-table-column title="标签名" data-index="name" />
                <a-table-column title="使用次数" data-index="usage_count" :width="100" />
              </template>
            </a-table>
          </div>
        </div>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.effect-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}
.effect-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.effect-spin {
  flex: 1;
  min-height: 0;
}
.effect-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.trending-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #374151;
}
.trending-cols {
  display: flex;
  gap: 14px;
}
.trend-col {
  flex: 1;
  min-width: 0;
}
.col-title {
  margin: 0 0 8px;
  font-size: 14px;
}
.col-title.rise {
  color: #e34948;
}
.col-title.fall {
  color: #1baf7a;
}
.combo-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.combo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 6px 10px;
}
.combo-tags {
  font-size: 13px;
}
.cov-cards {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.cov-card {
  flex: 1;
  min-width: 140px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 16px;
}
.cov-label {
  font-size: 12px;
  color: #6b7280;
}
.cov-value {
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}
.section-title {
  margin: 8px 0;
  font-size: 14px;
}
.source-grid {
  display: flex;
  gap: 14px;
}
.source-chart {
  flex: 1;
  min-width: 0;
}
.source-table {
  flex: 1;
  min-width: 0;
}
</style>
