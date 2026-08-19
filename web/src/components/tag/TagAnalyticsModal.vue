<script setup lang="ts">
/** 标签分析弹窗：热门排行 + 共现关系图 + 使用趋势。 */

import { computed, nextTick, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { EChartsOption } from 'echarts'
import {
  fetchCooccurrenceNetwork,
  fetchTopTags,
  fetchTagTrend,
  type CooccurrenceNetwork,
  type TopTag,
} from '@/api/tags'
import ArcoChart from '@/components/chart/ArcoChart.vue'

const show = defineModel<boolean>('show', { required: true })

const loading = ref(false)
const network = ref<CooccurrenceNetwork>({ nodes: [], edges: [] })
const topTags = ref<TopTag[]>([])
/** 趋势数据（按选中标签加载） */
const trendData = ref<Array<{ bucket: string; count: number }>>([])
const trendTag = ref<{ id: number; name: string } | null>(null)
const trendGranularity = ref<'month' | 'week' | 'day'>('month')

// ===== 类别 → 颜色（dataviz 参考色板，固定顺序，不循环） =====
const CATEGORY_COLORS: Record<string, string> = {
  style: '#2a78d6',
  item_type: '#eb6834',
  color: '#1baf7a',
  body_part: '#eda100',
  fit: '#e87ba4',
  attribute: '#008300',
  free: '#4a3aa7',
  outfit: '#e34948',
}
const SERIES_BLUE = '#2a78d6'
const MUTED_INK = '#898781'

function categoryColor(cat: string): string {
  return CATEGORY_COLORS[cat] || MUTED_INK
}

watch(show, (v) => {
  if (v) {
    // 等待 modal 动画与布局完成后再加载数据
    nextTick(() => setTimeout(load, 60))
  }
})

async function load() {
  loading.value = true
  try {
    const [net, top] = await Promise.all([fetchCooccurrenceNetwork(30, 1), fetchTopTags(20)])
    network.value = net
    topTags.value = top
    // 默认展示使用次数最多的标签趋势
    if (top.length > 0) {
      await loadTrend(top[0].id, top[0].name)
    }
  } catch {
    // 接口失败给出提示而非 unhandled rejection
    Message.error('标签分析数据加载失败')
    network.value = { nodes: [], edges: [] }
    topTags.value = []
  } finally {
    loading.value = false
  }
}

// ===== 热门标签排行（横向条形图） =====
const topOption = computed<EChartsOption | null>(() => {
  const tags = topTags.value
  if (tags.length === 0) return null
  // 横向：顶部是最多的，倒序让第一名在最上
  const sorted = [...tags].reverse()
  return {
    grid: { left: 8, right: 32, top: 8, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      axisLabel: { color: MUTED_INK },
      splitLine: { lineStyle: { color: '#e1e0d9' } },
    },
    yAxis: {
      type: 'category',
      data: sorted.map((t) => t.name),
      axisLabel: { color: '#52514e', fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    series: [
      {
        type: 'bar',
        data: sorted.map((t) => t.usage_count),
        itemStyle: { color: SERIES_BLUE, borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 14,
        label: { show: true, position: 'right', color: '#52514e', fontSize: 11 },
      },
    ],
  }
})

// ===== 共现关系图（力导向） =====
const graphOption = computed<EChartsOption | null>(() => {
  const net = network.value
  if (net.nodes.length === 0) return null
  const maxWeight = Math.max(1, ...net.edges.map((e) => e.weight))
  return {
    tooltip: {
      formatter: (p: any) => {
        if (p.dataType === 'node') return `${p.data.name}<br/>使用 ${p.data.usage_count} 次`
        if (p.dataType === 'edge') return `共现 ${p.data.weight} 次`
        return ''
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        force: { repulsion: 120, edgeLength: 80, gravity: 0.1 },
        label: { show: true, position: 'right', fontSize: 11, color: '#52514e' },
        emphasis: { focus: 'adjacency', label: { fontWeight: 'bold' } },
        data: net.nodes.map((n) => ({
          id: n.id,
          name: n.name,
          value: n.usage_count,
          symbolSize: 8 + Math.min(24, Math.sqrt(n.usage_count) * 4),
          itemStyle: { color: categoryColor(n.category) },
        })),
        links: net.edges.map((e) => ({
          source: e.source,
          target: e.target,
          value: e.weight,
          lineStyle: {
            opacity: 0.25 + (e.weight / maxWeight) * 0.55,
            width: 1 + (e.weight / maxWeight) * 3,
          },
        })),
        lineStyle: { color: '#c3c2b7', curveness: 0.1 },
      },
    ],
  } as EChartsOption
})

/** 共现图实例就绪：绑定节点点击事件（点击节点查看其趋势） */
function onGraphReady(chart: any) {
  chart.on('click', (params: any) => {
    if (params.dataType === 'node' && params.data?.id != null) {
      loadTrend(params.data.id, params.data.name)
    }
  })
}

// ===== 使用趋势（折线图） =====
const trendOption = computed<EChartsOption | null>(() => {
  const rows = trendData.value
  if (rows.length === 0) return null
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((p) => p.bucket),
      axisLabel: { color: MUTED_INK, fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: MUTED_INK } },
    tooltip: { trigger: 'axis' },
    series: [
      {
        type: 'line',
        data: rows.map((p) => p.count),
        smooth: true,
        symbolSize: 8,
        lineStyle: { width: 2, color: SERIES_BLUE },
        itemStyle: { color: SERIES_BLUE },
        areaStyle: { color: 'rgba(42,120,214,0.12)' },
      },
    ],
  }
})

async function loadTrend(tagId: number, name: string) {
  trendTag.value = { id: tagId, name }
  try {
    const data = await fetchTagTrend(tagId, trendGranularity.value)
    trendData.value = data.trend
  } catch {
    // 趋势加载失败静默处理（无历史数据时为空图）
    trendData.value = []
  }
}

function onGranularityChange() {
  if (trendTag.value) loadTrend(trendTag.value.id, trendTag.value.name)
}
</script>

<template>
  <a-modal
    v-model:visible="show"
    title="标签分析"
    :footer="false"
    :width="'90%'"
    :modal-style="{ maxWidth: '1200px', height: '84vh' }"
  >
    <a-spin :loading="loading">
      <div class="analytics">
        <!-- 顶部：热门排行 + 趋势 -->
        <div class="row-top">
          <div class="panel">
            <h4>热门标签 Top {{ topTags.length }}</h4>
            <ArcoChart :option="topOption" :height="260" empty-text="暂无标签数据" />
          </div>
          <div class="panel">
            <h4>
              使用趋势
              <template v-if="trendTag">— 「{{ trendTag.name }}」</template>
            </h4>
            <a-radio-group
              v-model="trendGranularity"
              type="button"
              size="mini"
              @change="onGranularityChange"
            >
              <a-radio value="day">日</a-radio>
              <a-radio value="week">周</a-radio>
              <a-radio value="month">月</a-radio>
            </a-radio-group>
            <ArcoChart :option="trendOption" :height="220" empty-text="暂无趋势数据" />
          </div>
        </div>
        <!-- 底部：共现关系图 -->
        <div class="panel panel-graph">
          <h4>标签共现关系图 <span class="hint">（点击节点查看其趋势）</span></h4>
          <ArcoChart
            :option="graphOption"
            :height="320"
            :on-ready="onGraphReady"
            empty-text="暂无共现数据"
          />
        </div>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.analytics {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}
.row-top {
  display: flex;
  gap: 12px;
  flex: 0 0 42%;
  min-height: 0;
}
.panel {
  flex: 1;
  min-width: 0;
  border: 1px solid #e1e0d9;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
}
.panel h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #0b0b0b;
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel h4 .hint {
  font-size: 12px;
  color: #898781;
  font-weight: normal;
}
.panel-graph {
  flex: 1;
  min-height: 0;
}
</style>
