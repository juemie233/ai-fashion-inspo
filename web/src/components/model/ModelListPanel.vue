<script setup lang="ts">
/** 模型管理面板：连接状态、GPU 显存（自动监控+趋势图）、模型列表（详情/更新/复制）、下载队列、使用统计。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { h, ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Tag, Button, Popconfirm, Message } from '@arco-design/web-vue'
import { storeToRefs } from 'pinia'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import apiClient from '@/api/client'
import { useNotification } from '@/composables/useNotification'
import { useGpuMonitor } from '@/composables/useGpuMonitor'
import { useAiModelsStore, type OllamaModel } from '@/stores/aiModels'
import {
  formatBytes,
  formatVram,
  formatMs,
  formatDate,
  formatUptime,
  normalizeModelName,
} from '@/utils/format'

echarts.use([LineChart, TooltipComponent, GridComponent, CanvasRenderer])

const { requestAndNotify } = useNotification()
const store = useAiModelsStore()
const { models, activeModel, embeddingModel, ollamaConnected, statusLoading } = storeToRefs(store)
const { refreshModels, setActiveModel, setEmbeddingModel, deleteModel } = store

// ===== 服务状态（Ollama 版本 + 运行时长） =====
const ollamaVersion = ref('')
const ollamaUptime = ref<number | null>(null)

async function loadAiStatus() {
  try {
    const { data } = await apiClient.get<{
      ollama_version: string
      ollama_uptime_seconds: number | null
    }>('/ai/status')
    ollamaVersion.value = data.ollama_version || ''
    ollamaUptime.value = data.ollama_uptime_seconds ?? null
  } catch {
    /* 静默 */
  }
}

/** 配置的文本嵌入模型是否缺失（未安装） */
const embeddingMissing = computed(() => {
  if (!ollamaConnected.value || !embeddingModel.value) return false
  return !models.value.some(
    (m) => normalizeModelName(m.name) === normalizeModelName(embeddingModel.value),
  )
})

// ===== GPU 显存监控（自动轮询 + 趋势图） =====
const { gpuStats, gpuHistory, unloadModel, startPolling, stopPolling } = useGpuMonitor(5000, 60)
const gpuChartRef = ref<HTMLDivElement | null>(null)
let gpuChart: echarts.ECharts | null = null

watch(gpuHistory, () => renderGpuChart(), { deep: true })

/** 渲染 GPU 显存短时趋势折线图 */
function renderGpuChart() {
  if (!gpuChartRef.value) return
  if (!gpuChart || gpuChart.isDisposed()) gpuChart = echarts.init(gpuChartRef.value)
  const points = gpuHistory.value
  gpuChart.setOption({
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: points.map((p) => p.time), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: 'MB', splitLine: { lineStyle: { color: '#eee' } } },
    series: [
      {
        name: '已用显存',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: points.map((p) => p.used_mb),
        itemStyle: { color: '#2a78d6' },
        areaStyle: { opacity: 0.12 },
      },
    ],
  })
}

function handleResize() {
  gpuChart?.resize()
}

// ===== 模型详情 =====
interface ModelDetail {
  name: string
  parameter_size: string
  quantization_level: string
  family: string
  families: string[]
  format: string
  parent_model: string
  architecture: string
  template: string
  system: string
  license: string
  modelfile: string
  parameters: string
}
const detailVisible = ref(false)
const detailLoading = ref(false)
const modelDetail = ref<ModelDetail | null>(null)

async function openDetail(name: string) {
  detailVisible.value = true
  detailLoading.value = true
  modelDetail.value = null
  try {
    const { data } = await apiClient.get<ModelDetail>(
      `/ai/models/${encodeURIComponent(name)}/detail`,
    )
    modelDetail.value = data
  } catch (e) {
    Message.error(getApiErrorMessage(e, '获取模型详情失败'))
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

// ===== 模型复制 =====
const copyVisible = ref(false)
const copySource = ref('')
const copyDestination = ref('')
const copying = ref(false)

function openCopy(name: string) {
  copySource.value = name
  copyDestination.value = ''
  copyVisible.value = true
}

async function doCopyModel() {
  const dest = copyDestination.value.trim()
  if (!dest) {
    Message.warning('请输入目标模型名称')
    return
  }
  copying.value = true
  try {
    const { data } = await apiClient.post<{ message: string }>('/ai/models/copy', null, {
      params: { source: copySource.value, destination: dest },
    })
    Message.success(data.message || '复制完成')
    copyVisible.value = false
    refreshModels()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '复制失败'))
  } finally {
    copying.value = false
  }
}

// ===== 下载队列（多模型排队下载 + 更新复用） =====
type DownloadEndpoint = 'pull' | 'update'
interface DownloadTask {
  key: number
  name: string
  endpoint: DownloadEndpoint
  status: 'waiting' | 'running'
  progress: number
  total: number
  statusText: string
  controller: AbortController | null
}
const downloadTasks = ref<DownloadTask[]>([])
const downloadName = ref('')
let downloadSeq = 0

/** 常用模型下拉（官方库热门列表） */
const POPULAR_MODELS = [
  { label: '视觉：minicpm-v:8b', value: 'minicpm-v:8b' },
  { label: '视觉：qwen2.5-vl:7b', value: 'qwen2.5-vl:7b' },
  { label: '视觉：llama3.2-vision:11b', value: 'llama3.2-vision:11b' },
  { label: '视觉：llava:7b', value: 'llava:7b' },
  { label: '视觉：llava:13b', value: 'llava:13b' },
  { label: '多模态：gemma3:4b', value: 'gemma3:4b' },
  { label: '多模态：gemma3:12b', value: 'gemma3:12b' },
  { label: '文本：llama3.2:3b', value: 'llama3.2:3b' },
  { label: '文本：qwen2.5:7b', value: 'qwen2.5:7b' },
  { label: '嵌入：nomic-embed-text', value: 'nomic-embed-text' },
  { label: '嵌入：mxbai-embed-large', value: 'mxbai-embed-large' },
]

const runningTask = computed(() => downloadTasks.value.find((t) => t.status === 'running'))

/** 加入下载队列（自动去重等待中/进行中的同名任务） */
function addDownload(nameArg: string, endpoint: DownloadEndpoint = 'pull') {
  const name = nameArg.trim()
  if (!name) return
  const exists = downloadTasks.value.some((t) => t.name === name)
  if (exists) {
    Message.warning(`模型「${name}」已在下载队列中`)
    return
  }
  const task: DownloadTask = {
    key: ++downloadSeq,
    name,
    endpoint,
    status: 'waiting',
    progress: 0,
    total: 0,
    statusText: '',
    controller: null,
  }
  downloadTasks.value.push(task)
  runNextDownload()
}

/** 若无进行中的任务，则取下一个等待任务开始执行 */
function runNextDownload() {
  if (downloadTasks.value.some((t) => t.status === 'running')) return
  const next = downloadTasks.value.find((t) => t.status === 'waiting')
  if (next) runDownload(next)
}

/** 执行单个下载任务（SSE 流式进度） */
async function runDownload(task: DownloadTask) {
  task.status = 'running'
  task.statusText = '连接中...'
  task.controller = new AbortController()
  const baseUrl = apiClient.defaults.baseURL || '/api'
  const url =
    task.endpoint === 'update'
      ? `${baseUrl}/ai/models/${encodeURIComponent(task.name)}/update`
      : `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(task.name)}`

  try {
    const response = await fetch(url, { method: 'POST', signal: task.controller.signal })
    if (!response.ok) {
      const errText = await response.text()
      let errMsg = '下载请求失败'
      try {
        errMsg = JSON.parse(errText).detail || errMsg
      } catch {
        /* 忽略 */
      }
      throw new Error(errMsg)
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('无法读取响应流')

    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          if (data.type === 'progress') {
            task.progress = data.completed || 0
            task.total = data.total || 0
            task.statusText = data.status || ''
          } else if (data.type === 'done') {
            task.statusText = '下载完成'
            Message.success(
              `模型「${task.name}」${task.endpoint === 'update' ? '更新' : '下载'}完成`,
            )
            requestAndNotify('模型下载完成', { body: `${task.name} 已就绪`, tag: 'model-download' })
            refreshModels()
            loadModelStats() // 下载完成后自动刷新使用统计
            downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
            runNextDownload()
            return
          } else if (data.type === 'error') {
            throw new Error(data.message || '下载失败')
          }
        } catch (parseErr) {
          const perr = parseErr as Error
          if (perr.message && !perr.message.includes('JSON')) {
            downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
            Message.error(perr.message)
            runNextDownload()
            return
          }
        }
      }
    }
    // 流意外结束
    downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
    runNextDownload()
  } catch (e) {
    const err = e as Error
    if (err.name === 'AbortError') {
      Message.info(`已取消「${task.name}」的下载`)
    } else {
      Message.error(getApiErrorMessage(e, '下载连接中断'))
    }
    downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
    runNextDownload()
  } finally {
    task.controller = null
  }
}

function startDownload() {
  addDownload(downloadName.value)
  downloadName.value = ''
}

function cancelDownload(key: number) {
  const task = downloadTasks.value.find((t) => t.key === key)
  if (task?.controller) task.controller.abort()
  else if (task) downloadTasks.value = downloadTasks.value.filter((t) => t.key !== key)
}

const downloadPercent = computed(() => {
  const task = runningTask.value
  if (!task || task.total === 0) return 0
  return Math.round((task.progress / task.total) * 100)
})

const downloadSize = computed(() => {
  const task = runningTask.value
  if (!task || task.total === 0) return ''
  return `${formatBytes(task.progress)} / ${formatBytes(task.total)}`
})

// ===== 模型统计 =====
interface ModelStat {
  model_name: string
  total_analyses: number
  success_count: number
  failure_count: number
  success_rate: number
  avg_time_ms: number
  avg_tags: number
  last_used: string
}
const modelStats = ref<ModelStat[]>([])
const totalAnalyses = ref(0)
const statsLoading = ref(false)

async function loadModelStats() {
  statsLoading.value = true
  try {
    const { data } = await apiClient.get<{ models: ModelStat[]; total_analyses: number }>(
      '/ai/model-stats',
    )
    modelStats.value = data.models
    totalAnalyses.value = data.total_analyses
  } catch {
    /* 忽略 */
  } finally {
    statsLoading.value = false
  }
}

/** 切换活跃模型（失败回滚由 store 处理） */
async function handleSetActiveModel(name: string) {
  const ok = await setActiveModel(name)
  if (ok) Message.success(`已切换到 ${name}`)
  else Message.error('切换失败')
}

/** 切换文本嵌入模型 */
async function handleSetEmbeddingModel(name: string) {
  const ok = await setEmbeddingModel(name)
  if (ok) Message.success(`已将 ${name} 设为文本嵌入模型`)
  else Message.error('切换嵌入模型失败')
}

/** 删除模型 */
async function handleDeleteModel(name: string) {
  const ok = await deleteModel(name)
  if (ok) Message.success(`已删除 ${name}`)
  else Message.error('删除失败')
}

onMounted(() => {
  refreshModels()
  loadAiStatus()
  loadModelStats()
  startPolling()
  nextTick(() => setTimeout(renderGpuChart, 30))
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  stopPolling()
  downloadTasks.value.forEach((t) => t.controller?.abort())
  window.removeEventListener('resize', handleResize)
  gpuChart?.dispose()
  gpuChart = null
})

/** 模型列表列定义 */
const modelColumns = [
  {
    title: '名称',
    dataIndex: 'name',
    width: 180,
    render: ({ record }: { record: unknown }) =>
      h(
        'span',
        { title: (record as OllamaModel).name, style: 'word-break:break-all' },
        (record as OllamaModel).name,
      ),
  },
  { title: '大小', dataIndex: 'size_display', width: 90 },
  {
    title: '显存占用',
    dataIndex: 'vram',
    width: 90,
    render: ({ record }: { record: unknown }) => {
      const r = record as OllamaModel
      return r.loaded ? formatVram(r.vram_used) : '-'
    },
  },
  {
    title: '状态',
    dataIndex: 'loaded',
    width: 90,
    render: ({ record }: { record: unknown }) => {
      const r = record as OllamaModel
      return r.is_active
        ? h(Tag, { color: 'green', size: 'small' }, () => '活跃')
        : r.is_embedding
          ? h(Tag, { color: 'arcoblue', size: 'small' }, () => '文本嵌入')
          : r.loaded
            ? h(Tag, { color: 'arcoblue', size: 'small' }, () => '已加载')
            : h(Tag, { size: 'small' }, () => '休眠')
    },
  },
  {
    title: '更新时间',
    dataIndex: 'modified',
    width: 110,
    render: ({ record }: { record: unknown }) => (record as OllamaModel).modified?.split('T')[0],
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 260,
    render: ({ record }: { record: unknown }) => {
      const r = record as OllamaModel
      return h('span', { style: 'display:flex;gap:4px;flex-wrap:wrap' }, [
        !r.is_active
          ? h(Button, { size: 'mini', onClick: () => handleSetActiveModel(r.name) }, () => '启用')
          : null,
        !r.is_embedding
          ? h(
              Button,
              { size: 'mini', type: 'secondary', onClick: () => handleSetEmbeddingModel(r.name) },
              () => '设嵌入',
            )
          : null,
        h(Button, { size: 'mini', type: 'text', onClick: () => openDetail(r.name) }, () => '详情'),
        h(
          Button,
          { size: 'mini', type: 'secondary', onClick: () => addDownload(r.name, 'update') },
          () => '更新',
        ),
        h(
          Button,
          { size: 'mini', type: 'secondary', onClick: () => openCopy(r.name) },
          () => '复制',
        ),
        !r.is_active
          ? h(
              Popconfirm,
              { content: '确定删除此模型？', onOk: () => handleDeleteModel(r.name) },
              {
                default: () =>
                  h(Button, { size: 'mini', type: 'secondary', status: 'danger' }, () => '删除'),
              },
            )
          : null,
      ])
    },
  },
]

/** 模型使用统计列定义 */
const statColumns = [
  { title: '模型', dataIndex: 'model_name', width: 160 },
  { title: '分析次数', dataIndex: 'total_analyses', width: 90 },
  {
    title: '成功率',
    dataIndex: 'success_rate',
    width: 90,
    render: ({ record }: { record: unknown }) => `${(record as ModelStat).success_rate}%`,
  },
  {
    title: '平均耗时',
    dataIndex: 'avg_time',
    width: 100,
    render: ({ record }: { record: unknown }) => formatMs((record as ModelStat).avg_time_ms),
  },
  { title: '平均标签', dataIndex: 'avg_tags', width: 90 },
  {
    title: '最近使用',
    dataIndex: 'last_used',
    width: 150,
    render: ({ record }: { record: unknown }) => {
      const r = record as ModelStat
      return r.last_used ? formatDate(r.last_used) : '-'
    },
  },
]
</script>

<template>
  <div>
    <!-- 连接状态 -->
    <a-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom: 16px">
      {{
        ollamaConnected
          ? `Ollama 已连接${ollamaVersion ? ` v${ollamaVersion}` : ''}${ollamaUptime != null ? ` · 已运行 ${formatUptime(ollamaUptime)}` : ''} · 活跃模型: ${activeModel}`
          : 'Ollama 未连接'
      }}
    </a-alert>

    <!-- 文本嵌入模型缺失告警（向量检索文本侧依赖） -->
    <a-alert v-if="embeddingMissing" type="warning" style="margin-bottom: 16px">
      <template #title>文本嵌入模型「{{ embeddingModel }}」未安装</template>
      向量检索的文本侧依赖该模型（文本搜索/混合排序），当前不可用。
      <a-button
        size="mini"
        type="primary"
        style="margin-left: 8px"
        @click="addDownload(embeddingModel)"
      >
        一键下载
      </a-button>
    </a-alert>

    <!-- GPU 显存监控（自动轮询 + 趋势图） -->
    <a-card v-if="gpuStats?.gpu_available" size="small" style="margin-bottom: 16px">
      <template #title>
        🖥 GPU 显存
        <a-tag color="arcoblue" size="small" style="margin-left: 8px">每 5 秒自动刷新</a-tag>
      </template>
      <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap">
        <span style="font-size: 13px; color: #666">{{ gpuStats?.gpu_name || 'GPU' }}</span>
        <a-progress
          type="line"
          :percent="gpuStats?.usage_percent || 0"
          :stroke-width="16"
          :status="
            (gpuStats?.usage_percent || 0) > 90
              ? 'danger'
              : (gpuStats?.usage_percent || 0) > 70
                ? 'warning'
                : 'success'
          "
          style="flex: 1; min-width: 200px"
        >
          <span style="font-size: 11px"
            >{{ gpuStats?.used_vram_mb }} / {{ gpuStats?.total_vram_mb || '?' }} MB</span
          >
        </a-progress>
        <span v-if="gpuStats?.free_vram_mb" style="font-size: 12px; color: #18a058"
          >空闲 {{ gpuStats?.free_vram_mb }} MB</span
        >
      </div>
      <div v-if="gpuStats?.loaded_models?.length" style="margin-top: 8px">
        <a-tag
          v-for="m in gpuStats.loaded_models"
          :key="m.name"
          closable
          size="small"
          @close="unloadModel(m.name)"
          style="margin: 2px"
        >
          {{ m.name }} ({{ m.vram_mb }} MB)
        </a-tag>
        <span style="font-size: 11px; color: #999; margin-left: 4px">点击 × 卸载模型</span>
      </div>
      <div v-if="gpuHistory.length > 1" ref="gpuChartRef" style="height: 180px; margin-top: 12px" />
      <div v-else style="font-size: 11px; color: #999; margin-top: 8px">正在收集显存趋势数据…</div>
    </a-card>

    <!-- 模型列表 -->
    <a-card title="已安装模型" size="small" style="margin-bottom: 16px">
      <template #extra>
        <a-button size="small" @click="refreshModels" :loading="statusLoading">刷新</a-button>
      </template>
      <a-table
        v-if="models.length"
        :columns="modelColumns"
        :data="models"
        :bordered="false"
        size="small"
        :pagination="false"
      />
      <a-empty v-else description="暂无已安装模型" />
    </a-card>

    <!-- 下载模型 -->
    <a-card title="下载新模型" size="small">
      <a-space align="center" :wrap="false">
        <a-select
          :options="POPULAR_MODELS"
          placeholder="选择常用模型"
          size="small"
          allow-clear
          style="width: 200px"
          @change="(v: unknown) => v && addDownload(String(v))"
        />
        <a-input
          v-model="downloadName"
          placeholder="如: gemma3:4b, llava:7b"
          style="width: 240px"
          @press-enter="startDownload"
        />
        <a-button type="primary" @click="startDownload" :disabled="!downloadName.trim()">
          加入下载队列
        </a-button>
      </a-space>

      <!-- 下载队列 -->
      <div v-if="downloadTasks.length" style="margin-top: 12px">
        <div
          v-for="t in downloadTasks"
          :key="t.key"
          style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px"
        >
          <span style="min-width: 180px; word-break: break-all">{{ t.name }}</span>
          <a-tag :color="t.status === 'running' ? 'arcoblue' : 'gray'" size="small">
            {{
              t.status === 'running' ? (t.endpoint === 'update' ? '更新中' : '下载中') : '等待中'
            }}
          </a-tag>
          <a-progress
            v-if="t.status === 'running'"
            type="line"
            :percent="t.key === runningTask?.key ? downloadPercent : 0"
            :stroke-width="12"
            style="flex: 1; min-width: 120px"
          />
          <span style="font-size: 12px; color: #666; min-width: 140px"
            >{{ t.statusText
            }}{{ t.key === runningTask?.key && downloadSize ? ` ${downloadSize}` : '' }}</span
          >
          <a-button size="mini" type="text" status="danger" @click="cancelDownload(t.key)"
            >取消</a-button
          >
        </div>
      </div>
      <p style="font-size: 12px; color: #999; margin-top: 8px">
        支持多模型排队下载：加入队列后按顺序逐个执行，完成后自动刷新使用统计。
      </p>
    </a-card>

    <!-- 模型使用统计 -->
    <a-card title="模型使用统计" size="small" style="margin-top: 16px">
      <template #extra>
        <a-button size="small" @click="loadModelStats" :loading="statsLoading">刷新</a-button>
      </template>
      <a-table
        v-if="modelStats.length"
        :columns="statColumns"
        :data="modelStats"
        :bordered="false"
        size="small"
        :pagination="false"
      />
      <a-empty v-else description="暂无分析数据">
        <a-button size="small" @click="loadModelStats">加载统计</a-button>
      </a-empty>
    </a-card>

    <!-- 模型详情弹窗 -->
    <a-modal v-model:visible="detailVisible" title="模型详情" :footer="false" :width="760">
      <a-spin :loading="detailLoading">
        <template v-if="modelDetail">
          <a-descriptions bordered :column="2" size="small">
            <a-descriptions-item label="模型名称">{{ modelDetail.name }}</a-descriptions-item>
            <a-descriptions-item label="参数量">{{
              modelDetail.parameter_size || '-'
            }}</a-descriptions-item>
            <a-descriptions-item label="量化方式">{{
              modelDetail.quantization_level || '-'
            }}</a-descriptions-item>
            <a-descriptions-item label="格式">{{ modelDetail.format || '-' }}</a-descriptions-item>
            <a-descriptions-item label="家族">{{ modelDetail.family || '-' }}</a-descriptions-item>
            <a-descriptions-item label="父模型">{{
              modelDetail.parent_model || '-'
            }}</a-descriptions-item>
            <a-descriptions-item label="架构">{{
              modelDetail.architecture || '-'
            }}</a-descriptions-item>
            <a-descriptions-item label="许可证">{{
              modelDetail.license || '-'
            }}</a-descriptions-item>
          </a-descriptions>
          <a-collapse style="margin-top: 12px">
            <a-collapse-item header="模板（Template）">
              <pre
                style="
                  font-size: 12px;
                  white-space: pre-wrap;
                  word-break: break-all;
                  background: #f5f5f5;
                  padding: 12px;
                  border-radius: 6px;
                  margin: 0;
                "
                >{{ modelDetail.template || '（无）' }}</pre>
            </a-collapse-item>
            <a-collapse-item header="Modelfile">
              <pre
                style="
                  font-size: 12px;
                  white-space: pre-wrap;
                  word-break: break-all;
                  background: #f5f5f5;
                  padding: 12px;
                  border-radius: 6px;
                  margin: 0;
                "
                >{{ modelDetail.modelfile || '（无）' }}</pre>
            </a-collapse-item>
          </a-collapse>
        </template>
        <a-empty v-else-if="!detailLoading" description="未获取到详情" />
      </a-spin>
    </a-modal>

    <!-- 复制模型弹窗 -->
    <a-modal v-model:visible="copyVisible" title="复制模型" :footer="false">
      <a-form
        :model="{ copySource, copyDestination }"
        label-align="left"
        :label-col-style="{ width: '90px' }"
      >
        <a-form-item label="源模型">
          <a-input :model-value="copySource" disabled />
        </a-form-item>
        <a-form-item label="目标名称">
          <a-input
            v-model="copyDestination"
            placeholder="如 qwen3-vl:latest"
            @press-enter="doCopyModel"
          />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button size="small" @click="copyVisible = false">取消</a-button>
        <a-button size="small" type="primary" :loading="copying" @click="doCopyModel"
          >复制</a-button
        >
      </template>
    </a-modal>
  </div>
</template>
