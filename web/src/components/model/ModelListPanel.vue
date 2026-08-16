<script setup lang="ts">
/** 模型管理面板：连接状态、GPU 显存（自动监控+趋势图）、模型列表（详情/更新/复制）、下载队列、使用统计。 */

import { h, ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { NTag, NButton, NPopconfirm, useMessage } from 'naive-ui'
import { storeToRefs } from 'pinia'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import apiClient from '@/api/client'
import { useNotification } from '@/composables/useNotification'
import { useGpuMonitor } from '@/composables/useGpuMonitor'
import { useAiModelsStore, type OllamaModel } from '@/stores/aiModels'
import { formatBytes, formatVram, formatMs, formatDate, formatUptime } from '@/utils/format'

echarts.use([LineChart, TooltipComponent, GridComponent, CanvasRenderer])

const message = useMessage()
const { requestAndNotify } = useNotification()
const store = useAiModelsStore()
const { models, activeModel, embeddingModel, ollamaConnected, statusLoading } = storeToRefs(store)
const { refreshModels, setActiveModel, setEmbeddingModel, deleteModel } = store

// ===== 服务状态（Ollama 版本 + 运行时长） =====
const ollamaVersion = ref('')
const ollamaUptime = ref<number | null>(null)

async function loadAiStatus() {
  try {
    const { data } = await apiClient.get<{ ollama_version: string; ollama_uptime_seconds: number | null }>('/ai/status')
    ollamaVersion.value = data.ollama_version || ''
    ollamaUptime.value = data.ollama_uptime_seconds ?? null
  } catch { /* 静默 */ }
}

/** 配置的文本嵌入模型是否缺失（未安装） */
const embeddingMissing = computed(() => {
  if (!ollamaConnected.value || !embeddingModel.value) return false
  return !models.value.some((m) => m.name === embeddingModel.value)
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
    const { data } = await apiClient.get<ModelDetail>(`/ai/models/${encodeURIComponent(name)}/detail`)
    modelDetail.value = data
  } catch (e: any) {
    message.error(e.response?.data?.detail || '获取模型详情失败')
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
    message.warning('请输入目标模型名称')
    return
  }
  copying.value = true
  try {
    const { data } = await apiClient.post<{ message: string }>('/ai/models/copy', null, {
      params: { source: copySource.value, destination: dest },
    })
    message.success(data.message || '复制完成')
    copyVisible.value = false
    refreshModels()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '复制失败')
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
    message.warning(`模型「${name}」已在下载队列中`)
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
  const url = task.endpoint === 'update'
    ? `${baseUrl}/ai/models/${encodeURIComponent(task.name)}/update`
    : `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(task.name)}`

  try {
    const response = await fetch(url, { method: 'POST', signal: task.controller.signal })
    if (!response.ok) {
      const errText = await response.text()
      let errMsg = '下载请求失败'
      try { errMsg = JSON.parse(errText).detail || errMsg } catch { /* 忽略 */ }
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
            message.success(`模型「${task.name}」${task.endpoint === 'update' ? '更新' : '下载'}完成`)
            requestAndNotify('模型下载完成', { body: `${task.name} 已就绪`, tag: 'model-download' })
            refreshModels()
            loadModelStats()  // 下载完成后自动刷新使用统计
            downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
            runNextDownload()
            return
          } else if (data.type === 'error') {
            throw new Error(data.message || '下载失败')
          }
        } catch (parseErr: any) {
          if (parseErr.message && !parseErr.message.includes('JSON')) {
            downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
            message.error(parseErr.message)
            runNextDownload()
            return
          }
        }
      }
    }
    // 流意外结束
    downloadTasks.value = downloadTasks.value.filter((t) => t.key !== task.key)
    runNextDownload()
  } catch (e: any) {
    if (e.name === 'AbortError') {
      message.info(`已取消「${task.name}」的下载`)
    } else {
      message.error(e.message || '下载连接中断')
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
  model_name: string; total_analyses: number; success_count: number
  failure_count: number; success_rate: number; avg_time_ms: number
  avg_tags: number; last_used: string
}
const modelStats = ref<ModelStat[]>([])
const totalAnalyses = ref(0)
const statsLoading = ref(false)

async function loadModelStats() {
  statsLoading.value = true
  try {
    const { data } = await apiClient.get<{ models: ModelStat[]; total_analyses: number }>('/ai/model-stats')
    modelStats.value = data.models
    totalAnalyses.value = data.total_analyses
  } catch { /* 忽略 */ }
  finally { statsLoading.value = false }
}

/** 切换活跃模型（失败回滚由 store 处理） */
async function handleSetActiveModel(name: string) {
  const ok = await setActiveModel(name)
  if (ok) message.success(`已切换到 ${name}`)
  else message.error('切换失败')
}

/** 切换文本嵌入模型 */
async function handleSetEmbeddingModel(name: string) {
  const ok = await setEmbeddingModel(name)
  if (ok) message.success(`已将 ${name} 设为文本嵌入模型`)
  else message.error('切换嵌入模型失败')
}

/** 删除模型 */
async function handleDeleteModel(name: string) {
  const ok = await deleteModel(name)
  if (ok) message.success(`已删除 ${name}`)
  else message.error('删除失败')
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
</script>

<template>
  <div>
    <!-- 连接状态 -->
    <n-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom:16px">
      {{ ollamaConnected ? `Ollama 已连接${ollamaVersion ? ` v${ollamaVersion}` : ''}${ollamaUptime != null ? ` · 已运行 ${formatUptime(ollamaUptime)}` : ''} · 活跃模型: ${activeModel}` : 'Ollama 未连接' }}
    </n-alert>

    <!-- 文本嵌入模型缺失告警（向量检索文本侧依赖） -->
    <n-alert v-if="embeddingMissing" type="warning" style="margin-bottom:16px">
      <template #header>文本嵌入模型「{{ embeddingModel }}」未安装</template>
      向量检索的文本侧依赖该模型（文本搜索/混合排序），当前不可用。
      <n-button size="tiny" type="primary" style="margin-left:8px" @click="addDownload(embeddingModel)">
        一键下载
      </n-button>
    </n-alert>

    <!-- GPU 显存监控（自动轮询 + 趋势图） -->
    <n-card v-if="gpuStats?.gpu_available" size="small" style="margin-bottom:16px">
      <template #header>
        🖥 GPU 显存
        <n-tag size="tiny" type="info" style="margin-left:8px">每 5 秒自动刷新</n-tag>
      </template>
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <span style="font-size:13px;color:#666">{{ gpuStats?.gpu_name || 'GPU' }}</span>
        <n-progress
          type="line"
          :percentage="gpuStats?.usage_percent || 0"
          :height="16"
          :status="(gpuStats?.usage_percent || 0) > 90 ? 'error' : (gpuStats?.usage_percent || 0) > 70 ? 'warning' : 'success'"
          style="flex:1;min-width:200px"
        >
          <template #default>
            <span style="font-size:11px">{{ gpuStats?.used_vram_mb }} / {{ gpuStats?.total_vram_mb || '?' }} MB</span>
          </template>
        </n-progress>
        <span v-if="gpuStats?.free_vram_mb" style="font-size:12px;color:#18a058">空闲 {{ gpuStats?.free_vram_mb }} MB</span>
      </div>
      <div v-if="gpuStats?.loaded_models?.length" style="margin-top:8px">
        <n-tag
          v-for="m in gpuStats.loaded_models"
          :key="m.name"
          closable
          size="small"
          @close="unloadModel(m.name)"
          style="margin:2px"
        >
          {{ m.name }} ({{ m.vram_mb }} MB)
        </n-tag>
        <span style="font-size:11px;color:#999;margin-left:4px">点击 × 卸载模型</span>
      </div>
      <div v-if="gpuHistory.length > 1" ref="gpuChartRef" style="height:180px;margin-top:12px" />
      <div v-else style="font-size:11px;color:#999;margin-top:8px">正在收集显存趋势数据…</div>
    </n-card>

    <!-- 模型列表 -->
    <n-card title="已安装模型" size="small" style="margin-bottom:16px">
      <template #header-extra>
        <n-button size="small" @click="refreshModels" :loading="statusLoading">刷新</n-button>
      </template>
      <n-data-table
        v-if="models.length"
        :columns="[
          { title: '名称', key: 'name', width: 180, render: (row: OllamaModel) => h('span', { title: row.name, style: 'word-break:break-all' }, row.name) },
          { title: '大小', key: 'size_display', width: 90 },
          { title: '显存占用', key: 'vram', width: 90, render: (row: OllamaModel) => row.loaded ? formatVram(row.vram_used) : '-' },
          { title: '状态', key: 'loaded', width: 90, render: (row: OllamaModel) => row.is_active ? h(NTag, {type:'success',size:'small'}, '活跃') : row.is_embedding ? h(NTag, {type:'info',size:'small'}, '文本嵌入') : row.loaded ? h(NTag, {type:'info',size:'small'}, '已加载') : h(NTag, {size:'small'}, '休眠') },
          { title: '更新时间', key: 'modified', width: 110, render: (row: OllamaModel) => row.modified?.split('T')[0] },
          { title: '操作', key: 'actions', width: 260, render: (row: OllamaModel) => h('span', {style:'display:flex;gap:4px;flex-wrap:wrap'}, [
            !row.is_active ? h(NButton, {size:'tiny',onClick:()=>handleSetActiveModel(row.name)}, '启用') : null,
            !row.is_embedding ? h(NButton, {size:'tiny',secondary:true,onClick:()=>handleSetEmbeddingModel(row.name)}, '设嵌入') : null,
            h(NButton, {size:'tiny',quaternary:true,onClick:()=>openDetail(row.name)}, '详情'),
            h(NButton, {size:'tiny',secondary:true,onClick:()=>addDownload(row.name, 'update')}, '更新'),
            h(NButton, {size:'tiny',secondary:true,onClick:()=>openCopy(row.name)}, '复制'),
            !row.is_active ? h(NPopconfirm, {onPositiveClick:()=>handleDeleteModel(row.name)},
              { trigger: ()=>h(NButton,{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此模型？' }
            ) : null,
          ]) },
        ]"
        :data="models" :bordered="false" size="small"
      />
      <n-empty v-else description="暂无已安装模型" size="small" />
    </n-card>

    <!-- 下载模型 -->
    <n-card title="下载新模型" size="small">
      <n-space align="center" :wrap="false">
        <n-select
          :options="POPULAR_MODELS"
          placeholder="选择常用模型"
          size="small"
          clearable
          style="width:200px"
          @update:value="(v: string | null) => v && addDownload(v)"
        />
        <n-input v-model:value="downloadName" placeholder="如: gemma3:4b, llava:7b" style="width:240px" @keyup.enter="startDownload" />
        <n-button type="primary" @click="startDownload" :disabled="!downloadName.trim()">
          加入下载队列
        </n-button>
      </n-space>

      <!-- 下载队列 -->
      <div v-if="downloadTasks.length" style="margin-top:12px">
        <div v-for="t in downloadTasks" :key="t.key" style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:13px">
          <span style="min-width:180px;word-break:break-all">{{ t.name }}</span>
          <n-tag :type="t.status === 'running' ? 'info' : 'default'" size="tiny">
            {{ t.status === 'running' ? (t.endpoint === 'update' ? '更新中' : '下载中') : '等待中' }}
          </n-tag>
          <n-progress
            v-if="t.status === 'running'"
            type="line"
            :percentage="t.key === runningTask?.key ? downloadPercent : 0"
            :height="12"
            style="flex:1;min-width:120px"
          />
          <span style="font-size:12px;color:#666;min-width:140px">{{ t.statusText }}{{ t.key === runningTask?.key && downloadSize ? ` ${downloadSize}` : '' }}</span>
          <n-button size="tiny" quaternary type="error" @click="cancelDownload(t.key)">取消</n-button>
        </div>
      </div>
      <p style="font-size:12px;color:#999;margin-top:8px">支持多模型排队下载：加入队列后按顺序逐个执行，完成后自动刷新使用统计。</p>
    </n-card>

    <!-- 模型使用统计 -->
    <n-card title="模型使用统计" size="small" style="margin-top:16px">
      <template #header-extra>
        <n-button size="small" @click="loadModelStats" :loading="statsLoading">刷新</n-button>
      </template>
      <n-data-table
        v-if="modelStats.length"
        :columns="[
          { title: '模型', key: 'model_name', width: 160 },
          { title: '分析次数', key: 'total_analyses', width: 90 },
          { title: '成功率', key: 'success_rate', width: 90, render: (row: ModelStat) => `${row.success_rate}%` },
          { title: '平均耗时', key: 'avg_time', width: 100, render: (row: ModelStat) => formatMs(row.avg_time_ms) },
          { title: '平均标签', key: 'avg_tags', width: 90 },
          { title: '最近使用', key: 'last_used', width: 150, render: (row: ModelStat) => row.last_used ? formatDate(row.last_used) : '-' },
        ]"
        :data="modelStats" :bordered="false" size="small"
      />
      <n-empty v-else description="暂无分析数据" size="small">
        <template #extra>
          <n-button size="small" @click="loadModelStats">加载统计</n-button>
        </template>
      </n-empty>
    </n-card>

    <!-- 模型详情弹窗 -->
    <n-modal v-model:show="detailVisible" preset="card" title="模型详情" style="max-width:760px">
      <n-spin :show="detailLoading">
        <template v-if="modelDetail">
          <n-descriptions bordered :column="2" size="small" label-placement="left">
            <n-descriptions-item label="模型名称">{{ modelDetail.name }}</n-descriptions-item>
            <n-descriptions-item label="参数量">{{ modelDetail.parameter_size || '-' }}</n-descriptions-item>
            <n-descriptions-item label="量化方式">{{ modelDetail.quantization_level || '-' }}</n-descriptions-item>
            <n-descriptions-item label="格式">{{ modelDetail.format || '-' }}</n-descriptions-item>
            <n-descriptions-item label="家族">{{ modelDetail.family || '-' }}</n-descriptions-item>
            <n-descriptions-item label="父模型">{{ modelDetail.parent_model || '-' }}</n-descriptions-item>
            <n-descriptions-item label="架构">{{ modelDetail.architecture || '-' }}</n-descriptions-item>
            <n-descriptions-item label="许可证">{{ modelDetail.license || '-' }}</n-descriptions-item>
          </n-descriptions>
          <n-collapse style="margin-top:12px">
            <n-collapse-item title="模板（Template）" name="template">
              <n-code :code="modelDetail.template || '（无）'" language="text" word-wrap />
            </n-collapse-item>
            <n-collapse-item title="Modelfile" name="modelfile">
              <n-code :code="modelDetail.modelfile || '（无）'" language="text" word-wrap />
            </n-collapse-item>
          </n-collapse>
        </template>
        <n-empty v-else-if="!detailLoading" description="未获取到详情" size="small" />
      </n-spin>
    </n-modal>

    <!-- 复制模型弹窗 -->
    <n-modal v-model:show="copyVisible" preset="dialog" title="复制模型">
      <n-form label-placement="left" label-width="90">
        <n-form-item label="源模型">
          <n-input :value="copySource" disabled />
        </n-form-item>
        <n-form-item label="目标名称">
          <n-input v-model:value="copyDestination" placeholder="如 qwen3-vl:latest" @keyup.enter="doCopyModel" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button size="small" @click="copyVisible = false">取消</n-button>
        <n-button size="small" type="primary" :loading="copying" @click="doCopyModel">复制</n-button>
      </template>
    </n-modal>
  </div>
</template>
