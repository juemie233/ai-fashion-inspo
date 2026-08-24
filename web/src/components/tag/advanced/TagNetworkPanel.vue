<script setup lang="ts">
/** 网络图面板：参数配置 → 异步分析 → 力导向图（社区着色 / 桥接高亮 / 点击看趋势）+ 社区列表。 */

import { computed, onBeforeUnmount, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { EChartsOption } from 'echarts'
import { getApiErrorMessage } from '@/utils/apiError'
import { analyzeNetwork, asNetworkResult } from '@/api/tagAdvanced'
import { fetchTagTrend } from '@/api/tags'
import { CATEGORY_LABELS } from '@/api/tags'
import { useTaskPolling } from '@/composables/useTaskPolling'
import ArcoChart from '@/components/chart/ArcoChart.vue'
import type { CommunityInfo, NetworkAnalysisResult, NetworkNode } from '@/types/tagAdvanced'

const { task, pollTask, stopPolling } = useTaskPolling()

// ── 参数 ──
const limit = ref(100)
const minCount = ref(2)
const category = ref<string | undefined>(undefined)
/** 每节点保留权重最高的 N 条边（缓解全连接稠密图的「网格状」显示） */
const maxEdgesPerNode = ref(10)

// ── 结果 ──
const result = ref<NetworkAnalysisResult | null>(null)
const analyzing = ref(false)

// ── 趋势弹窗 ──
const trendVisible = ref(false)
const trendTag = ref<{ id: number; name: string } | null>(null)
const trendData = ref<Array<{ bucket: string; count: number }>>([])
const trendLoading = ref(false)

const running = computed(
  () =>
    analyzing.value || Boolean(task.value && ['pending', 'running'].includes(task.value.status)),
)

// 社区色板（固定顺序，不循环；超出用灰色）
const COMMUNITY_COLORS = [
  '#2a78d6',
  '#eb6834',
  '#1baf7a',
  '#eda100',
  '#e87ba4',
  '#008300',
  '#4a3aa7',
  '#e34948',
  '#00a8a8',
  '#7a5dc7',
]
const BRIDGE_COLOR = '#e34948'
const MUTED_INK = '#898781'

function communityColor(cid: number): string {
  return COMMUNITY_COLORS[cid % COMMUNITY_COLORS.length] ?? MUTED_INK
}

/** 提交图分析 */
async function runAnalyze() {
  if (running.value) return
  analyzing.value = true
  try {
    const { task_id } = await analyzeNetwork({
      limit: limit.value,
      min_count: minCount.value,
      category: category.value ?? null,
      with_communities: true,
      with_centrality: true,
      max_edges_per_node: maxEdgesPerNode.value,
    })
    pollTask(task_id, (r) => {
      const data = asNetworkResult(r)
      result.value = data
      if (data) {
        Message.success(
          `图分析完成：${data.nodes.length} 个节点，${data.communities.length} 个社区`,
        )
      }
    })
  } catch (e) {
    Message.error(getApiErrorMessage(e, '提交图分析任务失败'))
  } finally {
    analyzing.value = false
  }
}

// ── 力导向图 option ──
const graphOption = computed<EChartsOption | null>(() => {
  const data = result.value
  if (!data || data.nodes.length === 0) return null
  const maxWeight = Math.max(1, ...data.edges.map((e) => e.weight))
  // 节点大小按使用次数相对映射（相对最大节点缩放，低频标签不再与大标签同尺寸）
  const maxUsage = Math.max(1, ...data.nodes.map((n) => n.usage_count))
  return {
    tooltip: {
      formatter: (p: any) => {
        if (p.dataType === 'node') {
          const n = p.data as NetworkNode
          return (
            `${n.name}<br/>使用 ${n.usage_count} 次 · 度 ${n.degree}` +
            (n.betweenness != null ? ` · 介数 ${n.betweenness}` : '') +
            (n.is_bridge ? '<br/><b style="color:#e34948">桥接节点</b>' : '')
          )
        }
        if (p.dataType === 'edge') return `共现 ${p.data.value} 次`
        return ''
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        force: { repulsion: 140, edgeLength: 90, gravity: 0.1 },
        label: { show: true, position: 'right', fontSize: 11, color: '#52514e' },
        emphasis: { focus: 'adjacency', label: { fontWeight: 'bold' } },
        data: data.nodes.map((n) => ({
          id: n.id,
          name: n.name,
          value: n.usage_count,
          symbolSize: 8 + 26 * Math.pow(n.usage_count / maxUsage, 0.5),
          itemStyle: {
            color: communityColor(n.community),
            borderColor: n.is_bridge ? BRIDGE_COLOR : '#fff',
            borderWidth: n.is_bridge ? 3 : 1,
          },
        })),
        links: data.edges.map((e) => ({
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

/** 图实例就绪：绑定节点点击 → 加载趋势弹窗 */
function onGraphReady(chart: any) {
  chart.on('click', (params: any) => {
    if (params.dataType === 'node' && params.data?.id != null) {
      loadTrend(params.data.id as number, params.data.name as string)
    }
  })
}

async function loadTrend(tagId: number, name: string) {
  trendTag.value = { id: tagId, name }
  trendVisible.value = true
  trendLoading.value = true
  trendData.value = []
  try {
    const data = await fetchTagTrend(tagId, 'month')
    trendData.value = data.trend
  } catch {
    trendData.value = []
  } finally {
    trendLoading.value = false
  }
}

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
        lineStyle: { width: 2, color: '#2a78d6' },
        itemStyle: { color: '#2a78d6' },
        areaStyle: { color: 'rgba(42,120,214,0.12)' },
      },
    ],
  } as EChartsOption
})

const communities = computed<CommunityInfo[]>(() => result.value?.communities ?? [])

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="network-panel">
    <!-- 参数区 -->
    <div class="net-params">
      <div class="param-item">
        <span class="param-label">Top-N 节点</span>
        <a-input-number v-model="limit" :min="20" :max="500" :step="10" style="width: 100px" />
      </div>
      <div class="param-item">
        <span class="param-label">最小共现</span>
        <a-input-number v-model="minCount" :min="1" :max="20" style="width: 80px" />
      </div>
      <div class="param-item">
        <span class="param-label">类别</span>
        <a-select
          v-model="category"
          :allow-clear="true"
          placeholder="全部类别"
          style="width: 130px"
        >
          <a-option v-for="(label, key) in CATEGORY_LABELS" :key="key" :value="key">
            {{ label }}
          </a-option>
        </a-select>
      </div>
      <div class="param-item">
        <span class="param-label">每节点连边</span>
        <a-input-number v-model="maxEdgesPerNode" :min="0" :max="50" style="width: 80px" />
        <span class="param-hint" style="font-size: 11px; color: #9ca3af"> （0 = 不剪枝） </span>
      </div>
      <a-button type="primary" :loading="running" @click="runAnalyze">
        {{ result ? '重新分析' : '开始分析' }}
      </a-button>
    </div>

    <div v-if="!result && !running" class="empty-tip">
      配置参数后点击「开始分析」，将基于标签共现网络叠加社区发现、中心度与桥接节点分析。
    </div>

    <template v-if="result">
      <div class="net-body">
        <!-- 力导向图 -->
        <div class="graph-area">
          <ArcoChart
            :option="graphOption"
            :height="480"
            :on-ready="onGraphReady"
            empty-text="暂无共现数据"
          />
          <div class="legend-hint">
            <span>节点大小 = 使用次数，颜色 = 社区</span>
            <span class="bridge-sample">红边 = 桥接节点（连接多个社区）</span>
            <span class="click-hint">点击节点查看使用趋势</span>
          </div>
        </div>

        <!-- 社区列表 -->
        <div class="comm-list">
          <div class="comm-title">社区（{{ communities.length }}）</div>
          <div v-for="c in communities" :key="c.id" class="comm-item">
            <span class="comm-dot" :style="{ background: communityColor(c.id) }" />
            <div class="comm-info">
              <div class="comm-head">社区 #{{ c.id }}（{{ c.size }} 个标签）</div>
              <div class="comm-tags">{{ c.top_tags.join('、') }}</div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 趋势弹窗 -->
    <a-modal
      v-model:visible="trendVisible"
      :title="`「${trendTag?.name ?? ''}」使用趋势`"
      :footer="false"
      :width="560"
    >
      <a-spin :loading="trendLoading">
        <ArcoChart :option="trendOption" :height="260" empty-text="暂无趋势数据" />
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.network-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  overflow-y: auto;
}
.net-params {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  flex-shrink: 0;
}
.param-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.param-label {
  font-size: 13px;
  color: #374151;
}
.empty-tip {
  padding: 40px;
  text-align: center;
  color: #9ca3af;
  font-size: 14px;
}
.net-body {
  display: flex;
  gap: 14px;
}
.graph-area {
  flex: 1;
  min-width: 0;
}
.legend-hint {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
  flex-wrap: wrap;
}
.bridge-sample {
  color: #e34948;
}
.comm-list {
  width: 220px;
  flex-shrink: 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
  overflow-y: auto;
  max-height: 520px;
}
.comm-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.comm-item {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #f0f0f0;
}
.comm-item:last-child {
  border-bottom: none;
}
.comm-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
}
.comm-head {
  font-size: 12px;
  color: #374151;
}
.comm-tags {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
}
</style>
