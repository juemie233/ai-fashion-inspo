<script setup lang="ts">
/** 模型管理面板：连接状态、GPU 显存、模型列表、下载、使用统计。 */

import { h, ref, computed, onMounted, onUnmounted } from 'vue'
import { NTag, NButton, NPopconfirm, useMessage } from 'naive-ui'
import { storeToRefs } from 'pinia'
import apiClient from '@/api/client'
import { useNotification } from '@/composables/useNotification'
import { useAiModelsStore, type OllamaModel } from '@/stores/aiModels'
import { formatBytes, formatVram, formatMs, formatDate } from '@/utils/format'

const message = useMessage()
const { requestAndNotify } = useNotification()
const store = useAiModelsStore()
// 用 storeToRefs 保持 ref 响应式（直接解构会拿到非响应式快照，导致「未连接」不更新）
const { models, activeModel, embeddingModel, ollamaConnected, statusLoading } = storeToRefs(store)
const { refreshModels, setActiveModel, setEmbeddingModel, deleteModel } = store

// ===== 服务状态（Ollama 版本） =====
const ollamaVersion = ref('')

async function loadAiStatus() {
  try {
    const { data } = await apiClient.get<{ ollama_version: string }>('/ai/status')
    ollamaVersion.value = data.ollama_version || ''
  } catch { /* 静默 */ }
}

/** 配置的文本嵌入模型是否缺失（未安装） */
const embeddingMissing = computed(() => {
  if (!ollamaConnected.value || !embeddingModel.value) return false
  return !models.value.some((m) => m.name === embeddingModel.value)
})

// ===== 下载 =====
const downloadName = ref('')
const downloadProgress = ref(0)
const downloadTotal = ref(0)
const downloadStatus = ref('')
const downloading = ref(false)
let downloadAbortController: AbortController | null = null
const timerRefs: ReturnType<typeof setTimeout>[] = []

async function startDownload(nameArg?: string) {
  const name = (nameArg ?? downloadName.value).trim()
  if (!name) return
  downloadName.value = name
  downloading.value = true
  downloadProgress.value = 0
  downloadTotal.value = 0
  downloadStatus.value = '连接中...'

  downloadAbortController = new AbortController()
  const baseUrl = apiClient.defaults.baseURL || '/api'
  const url = `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(name)}`

  try {
    const response = await fetch(url, {
      method: 'POST',
      signal: downloadAbortController.signal,
    })
    if (!response.ok) {
      const errText = await response.text()
      let errMsg = '下载请求失败'
      try { errMsg = JSON.parse(errText).detail || errMsg } catch {}
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
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'progress') {
              downloadProgress.value = data.completed || 0
              downloadTotal.value = data.total || 0
              downloadStatus.value = data.status || ''
            } else if (data.type === 'done') {
              downloading.value = false
              downloadStatus.value = '下载完成'
              message.success('模型下载完成')
              requestAndNotify('模型下载完成', { body: `${downloadName.value} 已就绪`, tag: 'model-download' })
              refreshModels()
              return
            } else if (data.type === 'error') {
              throw new Error(data.message || '下载失败')
            }
          } catch (parseErr: any) {
            if (parseErr.message && !parseErr.message.includes('JSON')) throw parseErr
          }
        }
      }
    }
  } catch (e: any) {
    if (e.name === 'AbortError') {
      downloadStatus.value = '已取消'
      message.info('下载已取消')
    } else {
      message.error(e.message || '下载连接中断')
    }
  } finally {
    downloading.value = false
    downloadAbortController = null
  }
}

function cancelDownload() {
  if (downloadAbortController) {
    downloadAbortController.abort()
    downloadAbortController = null
  }
  if (downloading.value) {
    downloading.value = false
    downloadStatus.value = '已取消'
    message.info('下载已取消')
    const timer = setTimeout(() => { if (downloadStatus.value === '已取消') downloadStatus.value = '' }, 5000)
    timerRefs.push(timer)
  }
}

const downloadPercent = computed(() => {
  if (downloadTotal.value === 0) return 0
  return Math.round((downloadProgress.value / downloadTotal.value) * 100)
})

const downloadSize = computed(() => {
  if (downloadTotal.value === 0) return ''
  return `${formatBytes(downloadProgress.value)} / ${formatBytes(downloadTotal.value)}`
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

// ===== GPU 显存监控 =====
interface GpuStats {
  gpu_available: boolean
  gpu_name: string
  total_vram_mb: number
  used_vram_mb: number
  free_vram_mb: number
  usage_percent: number
  loaded_models: Array<{ name: string; vram_mb: number; loaded_at: string | null }>
}
const gpuStats = ref<GpuStats | null>(null)

async function loadGpuStats() {
  try {
    const { data } = await apiClient.get<GpuStats>('/ai/gpu-stats')
    gpuStats.value = data
  } catch { /* 静默失败 */ }
}

async function unloadModel(name: string) {
  try {
    const baseUrl = apiClient.defaults.baseURL || ''
    const resp = await fetch(`${baseUrl}/ai/unload-model?model_name=${encodeURIComponent(name)}`, { method: 'POST' })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    message.success(`正在卸载 ${name}...`)
    const timer = setTimeout(() => { if (gpuStats.value) loadGpuStats() }, 2000)
    timerRefs.push(timer)
  } catch (e: any) {
    message.error(e.message || '卸载模型失败')
    loadGpuStats()
  }
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
  loadGpuStats()
  loadAiStatus()
})

onUnmounted(() => {
  cancelDownload()
  timerRefs.forEach(clearTimeout)
  timerRefs.length = 0
})
</script>

<template>
  <div>
    <!-- 连接状态 -->
    <n-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom:16px">
      {{ ollamaConnected ? `Ollama 已连接${ollamaVersion ? ` v${ollamaVersion}` : ''} · 活跃模型: ${activeModel}` : 'Ollama 未连接' }}
    </n-alert>

    <!-- 文本嵌入模型缺失告警（向量检索文本侧依赖） -->
    <n-alert v-if="embeddingMissing" type="warning" style="margin-bottom:16px">
      <template #header>文本嵌入模型「{{ embeddingModel }}」未安装</template>
      向量检索的文本侧依赖该模型（文本搜索/混合排序），当前不可用。
      <n-button size="tiny" type="primary" style="margin-left:8px" :disabled="downloading" @click="startDownload(embeddingModel)">
        一键下载
      </n-button>
    </n-alert>

    <!-- GPU 显存监控 -->
    <n-card v-if="gpuStats?.gpu_available" size="small" style="margin-bottom:16px">
      <template #header>
        🖥 GPU 显存
        <n-button size="tiny" style="margin-left:8px" @click="loadGpuStats">刷新</n-button>
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
    </n-card>

    <!-- 模型列表 -->
    <n-card title="已安装模型" size="small" style="margin-bottom:16px">
      <template #header-extra>
        <n-button size="small" @click="refreshModels" :loading="statusLoading">刷新</n-button>
      </template>
      <n-data-table
        v-if="models.length"
        :columns="[
          { title: '名称', key: 'name', width: 200 },
          { title: '大小', key: 'size_display', width: 100 },
          { title: '显存占用', key: 'vram', width: 100, render: (row: OllamaModel) => row.loaded ? formatVram(row.vram_used) : '-' },
          { title: '状态', key: 'loaded', width: 100, render: (row: OllamaModel) => row.is_active ? h(NTag, {type:'success',size:'small'}, '活跃') : row.is_embedding ? h(NTag, {type:'info',size:'small'}, '文本嵌入') : row.loaded ? h(NTag, {type:'info',size:'small'}, '已加载') : h(NTag, {size:'small'}, '休眠') },
          { title: '更新时间', key: 'modified', width: 160, render: (row: OllamaModel) => row.modified?.split('T')[0] },
          { title: '操作', key: 'actions', render: (row: OllamaModel) => h('span', {style:'display:flex;gap:6px;flex-wrap:wrap'}, [
            !row.is_active ? h(NButton, {size:'tiny',onClick:()=>handleSetActiveModel(row.name)}, '启用') : null,
            !row.is_embedding ? h(NButton, {size:'tiny',secondary:true,onClick:()=>handleSetEmbeddingModel(row.name)}, '设嵌入') : null,
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
      <n-space align="center">
        <n-input v-model:value="downloadName" placeholder="如: gemma3:4b, llava:7b" style="width:280px" @keyup.enter="startDownload" />
        <n-button v-if="!downloading" type="primary" @click="startDownload()" :disabled="!downloadName.trim()">
          下载
        </n-button>
        <n-button v-else type="warning" @click="cancelDownload">取消下载</n-button>
      </n-space>
      <div v-if="downloading || downloadStatus === '下载完成' || downloadStatus === '已取消'" style="margin-top:12px">
        <n-progress type="line" :percentage="downloadPercent" :height="18" :status="downloadStatus === '下载完成' ? 'success' : downloadStatus === '已取消' ? 'warning' : undefined" />
        <p style="font-size:12px;color:#666;margin:4px 0">{{ downloadStatus }} {{ downloadSize }}</p>
      </div>
      <p style="font-size:12px;color:#999;margin-top:8px">常用模型: gemma3:4b, llava:7b, llava:13b, minicpm-v:8b · 文本嵌入: all-minilm</p>
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
  </div>
</template>
