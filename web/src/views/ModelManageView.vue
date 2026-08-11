<script setup lang="ts">
/** AI 模型管理页：状态面板、模型列表、下载、分析队列、历史、参数调优。 */

import { h, ref, onMounted, onUnmounted, computed } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()

// ===== 模型状态 =====
interface OllamaModel {
  name: string; size_bytes: number; size_display: string
  modified: string; is_active: boolean; vram_used: number; loaded: boolean
}
interface ModelListResponse { models: OllamaModel[]; active_model: string }

const models = ref<OllamaModel[]>([])
const activeModel = ref('')
const ollamaConnected = ref(false)
const statusLoading = ref(false)

// ===== 下载 =====
const downloadName = ref('')
const downloadProgress = ref(0)
const downloadTotal = ref(0)
const downloadStatus = ref('')
const downloading = ref(false)
let downloadEventSource: EventSource | null = null

// ===== 分析队列 =====
interface QueueStats { total: number; analyzed: number; unanalyzed: number; failed: number }
interface ActiveAnalysis { active_analyses: Record<string, string>; count: number }
const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
const activeAnalyses = ref<Record<string, string>>({})
const batchAnalyzing = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// ===== 分析历史 =====
interface HistoryItem {
  id: number; inspiration_id: string; model_name: string
  thumbnail_path: string | null; file_path: string | null
  processing_time_ms: number | null; error: string | null
  status: string; created_at: string
}
const history = ref<HistoryItem[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyPageSize = 20
const historyFilter = ref<string | null>(null)
const historyLoading = ref(false)

// ===== 分析详情弹窗 =====
interface TagDetail { name: string; category: string; confidence: number }
interface AnalysisDetail {
  id: number; inspiration_id: string; model_name: string
  raw_response: string | null; parsed_response: Record<string, any> | null
  processing_time_ms: number | null; error: string | null
  status: string; created_at: string | null
  thumbnail_path: string | null; file_path: string | null
  tags: TagDetail[]
}
const detailVisible = ref(false)
const detailLoading = ref(false)
const currentDetail = ref<AnalysisDetail | null>(null)

// ===== 参数 =====
interface AiSettings { active_model: string; confidence_threshold: number; analysis_timeout: number; ollama_base_url: string }
interface SamplingParams { temperature: number; top_p: number; top_k: number; num_predict: number }
const aiSettings = ref<AiSettings>({ active_model: '', confidence_threshold: 0.6, analysis_timeout: 60, ollama_base_url: '' })
const confThreshold = ref(0.6)
const analysisTimeout = ref(60)
const samplingParams = ref<SamplingParams>({ temperature: 0.7, top_p: 0.9, top_k: 40, num_predict: 1024 })
const defaultParams = { confidence_threshold: 0.6, analysis_timeout: 60, temperature: 0.7, top_p: 0.9, top_k: 40, num_predict: 1024 }
const persistSettings = ref(false)
const savingSettings = ref(false)

// ===== 标签页 =====
const activeTab = ref('models')

onMounted(() => {
  refreshModels()
  loadQueue()
  loadHistory()
  loadSettings()
  loadSamplingParams()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
  cancelDownload()
})

// ---- 模型列表 ----
async function refreshModels() {
  statusLoading.value = true
  try {
    const { data } = await apiClient.get<ModelListResponse>('/ai/models')
    models.value = data.models
    activeModel.value = data.active_model
    ollamaConnected.value = true
  } catch {
    ollamaConnected.value = false
  } finally {
    statusLoading.value = false
  }
}

async function setActiveModel(name: string) {
  try {
    await apiClient.put('/ai/models/active', null, { params: { model_name: name } })
    activeModel.value = name
    aiSettings.value.active_model = name
    message.success(`已切换到 ${name}`)
    refreshModels()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换失败')
  }
}

async function deleteModel(name: string) {
  try {
    await apiClient.delete(`/ai/models/${encodeURIComponent(name)}`)
    message.success(`已删除 ${name}`)
    refreshModels()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

// ---- 下载模型 ----
function startDownload() {
  if (!downloadName.value.trim()) return
  downloading.value = true
  downloadProgress.value = 0
  downloadTotal.value = 0
  downloadStatus.value = '连接中...'

  const baseUrl = apiClient.defaults.baseURL || '/api'
  const url = `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(downloadName.value.trim())}`
  downloadEventSource = new EventSource(url)

  downloadEventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'progress') {
        downloadProgress.value = data.completed || 0
        downloadTotal.value = data.total || 0
        downloadStatus.value = data.status || ''
      } else if (data.type === 'done') {
        downloading.value = false
        downloadStatus.value = '下载完成'
        downloadEventSource?.close()
        downloadEventSource = null
        message.success('模型下载完成')
        refreshModels()
      } else if (data.type === 'error') {
        downloading.value = false
        downloadEventSource?.close()
        downloadEventSource = null
        message.error(data.message || '下载失败')
      }
    } catch {}
  }
  downloadEventSource.onerror = () => {
    if (downloading.value) {
      downloading.value = false
      downloadEventSource?.close()
      downloadEventSource = null
      message.error('下载连接中断')
    }
  }
}

function cancelDownload() {
  if (downloadEventSource) {
    downloadEventSource.close()
    downloadEventSource = null
  }
  if (downloading.value) {
    downloading.value = false
    downloadStatus.value = '已取消'
    message.info('下载已取消')
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

// ---- 分析队列 ----
async function loadQueue() {
  try {
    const { data } = await apiClient.get<QueueStats>('/ai/queue')
    queueStats.value = data
  } catch {}
}

async function loadActiveAnalyses() {
  try {
    const { data } = await apiClient.get<ActiveAnalysis>('/ai/active-analyses')
    activeAnalyses.value = data.active_analyses || {}
  } catch {}
}

function startPolling() {
  loadActiveAnalyses()
  pollTimer = setInterval(() => {
    loadActiveAnalyses()
    if (Object.keys(activeAnalyses.value).length > 0) {
      loadQueue()
      // 分析进行中时自动刷新历史列表
      loadHistory()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function triggerBatchAnalyze() {
  batchAnalyzing.value = true
  try {
    // 获取所有未分析的素材（通过查询所有素材，排除已分析的）
    const { data: allInsp } = await apiClient.get('/inspirations', { params: { page: 1, size: 9999 } })
    const allIds: string[] = allInsp.items.map((i: any) => i.id)

    // 获取已分析的素材 ID
    const { data: analyzedData } = await apiClient.get('/ai/history', { params: { page: 1, size: 9999 } })
    const analyzedIds = new Set(analyzedData.items.map((h: any) => h.inspiration_id))

    const unanalyzedIds = allIds.filter(id => !analyzedIds.has(id))
    if (unanalyzedIds.length === 0) {
      message.info('所有素材均已分析过，无需重复分析')
      return
    }

    await apiClient.post('/ai/batch-analyze', unanalyzedIds)
    message.success(`已将 ${unanalyzedIds.length} 个素材加入分析队列`)
    loadQueue()
    loadHistory()
    loadActiveAnalyses()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '批量分析失败')
  } finally {
    batchAnalyzing.value = false
  }
}

async function retryAnalysis(id: string) {
  try {
    await apiClient.post(`/ai/retry/${id}`)
    message.success('已重新加入队列')
    loadQueue()
    loadActiveAnalyses()
  } catch {}
}

// ---- 分析历史 ----
async function loadHistory() {
  historyLoading.value = true
  try {
    const params: any = { page: historyPage.value, size: historyPageSize }
    if (historyFilter.value) params.status = historyFilter.value
    const { data } = await apiClient.get('/ai/history', { params })
    history.value = data.items
    historyTotal.value = data.total
  } catch {} finally {
    historyLoading.value = false
  }
}

function filterHistory(status: string | null) {
  historyFilter.value = status
  historyPage.value = 1
  loadHistory()
}

function onHistoryPageChange(page: number) {
  historyPage.value = page
  loadHistory()
}

async function viewDetail(logId: number) {
  detailVisible.value = true
  detailLoading.value = true
  currentDetail.value = null
  try {
    const { data } = await apiClient.get<AnalysisDetail>(`/ai/history/${logId}`)
    currentDetail.value = data
  } catch (e: any) {
    message.error(e.response?.data?.detail || '获取详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function deleteLog(logId: number) {
  try {
    await apiClient.delete(`/ai/history/${logId}`)
    message.success('分析记录已删除')
    loadHistory()
    loadQueue()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

const clearingFailed = ref(false)
async function deleteAllFailed() {
  clearingFailed.value = true
  try {
    const { data } = await apiClient.delete('/ai/history/failed/all')
    message.success(data.message || '已清空失败记录')
    loadHistory()
    loadQueue()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '清空失败')
  } finally {
    clearingFailed.value = false
  }
}

// 标签类别中文映射
const tagCategoryLabel: Record<string, string> = {
  style: '风格', item_type: '单品类型', color: '颜色', fit: '版型',
  body_part: '穿着方式', occasion: '场合', season: '季节', attribute: '属性',
}

// ---- 参数设置 ----
async function loadSettings() {
  try {
    const { data } = await apiClient.get<AiSettings>('/ai/settings')
    aiSettings.value = data
    confThreshold.value = data.confidence_threshold
    analysisTimeout.value = data.analysis_timeout
  } catch {}
}

async function loadSamplingParams() {
  try {
    const { data } = await apiClient.get<SamplingParams>('/ai/sampling-params')
    samplingParams.value = data
  } catch {}
}

async function saveSettings() {
  savingSettings.value = true
  try {
    await apiClient.put('/ai/settings', null, {
      params: {
        confidence_threshold: confThreshold.value,
        analysis_timeout: analysisTimeout.value,
        persist: persistSettings.value,
      },
    })
    await apiClient.put('/ai/sampling-params', null, {
      params: {
        temperature: samplingParams.value.temperature,
        top_p: samplingParams.value.top_p,
        top_k: samplingParams.value.top_k,
        num_predict: samplingParams.value.num_predict,
        persist: persistSettings.value,
      },
    })
    message.success('参数已保存' + (persistSettings.value ? '并持久化到 .env' : ''))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingSettings.value = false
  }
}

function resetToDefaults() {
  confThreshold.value = defaultParams.confidence_threshold
  analysisTimeout.value = defaultParams.analysis_timeout
  samplingParams.value = {
    temperature: defaultParams.temperature,
    top_p: defaultParams.top_p,
    top_k: defaultParams.top_k,
    num_predict: defaultParams.num_predict,
  }
  message.info('已恢复默认值（需点击保存生效）')
}

// ---- 工具函数 ----
function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(1)} ${units[i]}`
}
function formatVram(bytes: number) {
  if (!bytes || bytes === 0) return '-'
  return formatBytes(bytes)
}
function formatMs(ms: number | null) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function formatDate(d: string | null | undefined) {
  if (!d) return '-'
  try {
    const date = new Date(d)
    if (isNaN(date.getTime())) return '-'
    return date.toLocaleString('zh-CN')
  } catch { return '-' }
}
</script>

<template>
  <div class="model-page">
    <h2>AI 模型管理</h2>

    <n-tabs v-model:value="activeTab" type="line">
      <!-- ===== Tab: 模型 ===== -->
      <n-tab-pane name="models" tab="模型管理">
        <!-- 连接状态 -->
        <n-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom:16px">
          {{ ollamaConnected ? `Ollama 已连接 · 活跃模型: ${activeModel}` : 'Ollama 未连接' }}
        </n-alert>

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
              { title: '显存占用', key: 'vram', width: 100, render: (_:any, r:OllamaModel) => r.loaded ? formatVram(r.vram_used) : '-' },
              { title: '状态', key: 'loaded', width: 80, render: (_:any, r:OllamaModel) => r.is_active ? h('n-tag', {type:'success',size:'small'}, '活跃') : r.loaded ? h('n-tag', {type:'info',size:'small'}, '已加载') : h('n-tag', {size:'small'}, '休眠') },
              { title: '更新时间', key: 'modified', width: 160, render: (_:any, r:OllamaModel) => r.modified?.split('T')[0] },
              { title: '操作', key: 'actions', render: (_:any, r:OllamaModel) => h('span', {style:'display:flex;gap:6px'}, [
                !r.is_active ? h('n-button', {size:'tiny',onClick:()=>setActiveModel(r.name)}, '启用') : null,
                !r.is_active ? h('n-popconfirm', {onPositiveClick:()=>deleteModel(r.name)},
                  { trigger: ()=>h('n-button',{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此模型？' }
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
            <n-input v-model:value="downloadName" placeholder="如: gemma3:4b, llava:7b" style="width:280px" />
            <n-button v-if="!downloading" type="primary" @click="startDownload" :disabled="!downloadName.trim()">
              下载
            </n-button>
            <n-button v-else type="warning" @click="cancelDownload">取消下载</n-button>
          </n-space>
          <div v-if="downloading || downloadStatus === '下载完成' || downloadStatus === '已取消'" style="margin-top:12px">
            <n-progress type="line" :percentage="downloadPercent" :height="18" :status="downloadStatus === '下载完成' ? 'success' : downloadStatus === '已取消' ? 'warning' : undefined" />
            <p style="font-size:12px;color:#666;margin:4px 0">{{ downloadStatus }} {{ downloadSize }}</p>
          </div>
          <p style="font-size:12px;color:#999;margin-top:8px">常用模型: gemma3:4b, llava:7b, llava:13b, minicpm-v:8b</p>
        </n-card>
      </n-tab-pane>

      <!-- ===== Tab: 分析队列 ===== -->
      <n-tab-pane name="queue" tab="分析进度">
        <!-- 统计卡片 -->
        <n-grid :cols="4" :x-gap="12" style="margin-bottom:16px">
          <n-gi><n-card size="small"><n-statistic label="总素材" :value="queueStats.total" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="已分析" :value="queueStats.analyzed" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="未分析" :value="queueStats.unanalyzed" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="失败" :value="queueStats.failed" /></n-card></n-gi>
        </n-grid>

        <!-- 进度条 + 操作 -->
        <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
          <n-progress
            v-if="queueStats.total > 0"
            type="line"
            :percentage="Math.round(queueStats.analyzed / queueStats.total * 100)"
            :height="24"
            style="flex:1"
          />
          <n-button
            type="primary"
            @click="triggerBatchAnalyze"
            :loading="batchAnalyzing"
            :disabled="queueStats.unanalyzed === 0"
          >
            {{ queueStats.unanalyzed > 0 ? `分析全部未分析 (${queueStats.unanalyzed})` : '全部已分析' }}
          </n-button>
        </div>

        <!-- 正在分析提示 -->
        <n-alert v-if="Object.keys(activeAnalyses).length > 0" type="info" style="margin-bottom:16px" closable>
          <template #header>正在分析 {{ Object.keys(activeAnalyses).length }} 个素材...</template>
          <div v-for="(status, id) in activeAnalyses" :key="id" style="font-size:12px;color:#666">
            素材 {{ id.slice(0, 8) }}... — {{ status }}
          </div>
        </n-alert>

        <!-- 分析历史 -->
        <n-card title="分析历史" size="small">
          <template #header-extra>
            <n-space :size="8">
              <n-popconfirm @positive-click="deleteAllFailed">
                <template #trigger>
                  <n-button size="small" type="error" secondary :loading="clearingFailed">
                    删除所有失败记录 {{ queueStats.failed > 0 ? `(${queueStats.failed})` : '' }}
                  </n-button>
                </template>
                确定要删除所有失败记录吗？此操作不可恢复。
              </n-popconfirm>
              <n-button size="small" @click="loadHistory" :loading="historyLoading">刷新</n-button>
            </n-space>
          </template>

          <n-radio-group v-model:value="historyFilter" @update:value="filterHistory" size="small" style="margin-bottom:12px">
            <n-radio-button :value="null">全部</n-radio-button>
            <n-radio-button value="success">成功</n-radio-button>
            <n-radio-button value="error">失败</n-radio-button>
          </n-radio-group>

          <n-data-table
            v-if="history.length"
            :columns="[
              { title: '预览', key: 'thumbnail', width: 70, render: (_:any, r:HistoryItem) => r.thumbnail_path ? h('img', {src:getFileUrl(r.thumbnail_path), style:'width:48px;height:72px;object-fit:cover;border-radius:4px'}) : '-' },
              { title: '模型', key: 'model_name', width: 130 },
              { title: '状态', key: 'status', width: 70, render: (_:any, r:HistoryItem) => h('n-tag', {type:r.status==='success'?'success':'error',size:'small'}, r.status==='success'?'成功':'失败') },
              { title: '耗时', key: 'time', width: 80, render: (_:any, r:HistoryItem) => formatMs(r.processing_time_ms) },
              { title: '时间', key: 'created_at', width: 160, render: (_:any, r:HistoryItem) => formatDate(r.created_at) },
              { title: '操作', key: 'actions', width: 140, render: (_:any, r:HistoryItem) => h('span', {style:'display:flex;gap:4px'}, [
                r.status === 'success' ? h('n-button', {size:'tiny',onClick:()=>viewDetail(r.id)}, '详情') : null,
                r.status === 'error' ? h('n-button', {size:'tiny',onClick:()=>retryAnalysis(r.inspiration_id)}, '重试') : null,
                h('n-popconfirm', {onPositiveClick:()=>deleteLog(r.id)},
                  { trigger: ()=>h('n-button',{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此记录？' }
                ),
              ]) },
            ]"
            :data="history" :bordered="false" size="small"
            :loading="historyLoading"
          />
          <n-empty v-else description="暂无分析记录" size="small" />

          <!-- 分页 -->
          <div v-if="historyTotal > historyPageSize" style="display:flex;justify-content:center;margin-top:16px">
            <n-pagination
              :page="historyPage"
              :page-size="historyPageSize"
              :item-count="historyTotal"
              @update:page="onHistoryPageChange"
              size="small"
            />
          </div>
        </n-card>
      </n-tab-pane>

      <!-- ===== Tab: 参数设置 ===== -->
      <n-tab-pane name="settings" tab="参数调优">
        <n-space vertical :size="16" style="max-width:560px">
          <!-- 基础参数 -->
          <n-card title="基础参数" size="small">
            <n-form label-placement="left" label-width="110">
              <n-form-item label="活跃模型">
                <n-select
                  v-model:value="aiSettings.active_model"
                  :options="models.map(m=>({label:m.name,value:m.name}))"
                  placeholder="选择模型"
                  filterable
                  @update:value="setActiveModel"
                />
              </n-form-item>
              <n-form-item label="置信度阈值">
                <n-slider v-model:value="confThreshold" :min="0" :max="1" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
                <span style="margin-left:12px;font-size:13px;color:#666">{{ confThreshold.toFixed(2) }}</span>
              </n-form-item>
              <n-form-item label="分析超时 (秒)">
                <n-input-number v-model:value="analysisTimeout" :min="10" :max="300" style="width:120px" />
              </n-form-item>
              <n-form-item label="Ollama 地址">
                <n-input :value="aiSettings.ollama_base_url" readonly />
              </n-form-item>
            </n-form>
          </n-card>

          <!-- 采样参数 -->
          <n-card title="采样参数" size="small">
            <n-form label-placement="left" label-width="110">
              <n-form-item label="Temperature">
                <n-slider v-model:value="samplingParams.temperature" :min="0" :max="2" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
                <span style="margin-left:12px;font-size:13px;color:#666">{{ samplingParams.temperature.toFixed(2) }}</span>
              </n-form-item>
              <n-form-item label="Top P">
                <n-slider v-model:value="samplingParams.top_p" :min="0" :max="1" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
                <span style="margin-left:12px;font-size:13px;color:#666">{{ samplingParams.top_p.toFixed(2) }}</span>
              </n-form-item>
              <n-form-item label="Top K">
                <n-input-number v-model:value="samplingParams.top_k" :min="1" :max="100" style="width:120px" />
              </n-form-item>
              <n-form-item label="Max Tokens">
                <n-input-number v-model:value="samplingParams.num_predict" :min="64" :max="8192" :step="64" style="width:140px" />
              </n-form-item>
            </n-form>
          </n-card>

          <!-- 操作 -->
          <n-card size="small">
            <n-space align="center">
              <n-switch v-model:value="persistSettings" />
              <span style="font-size:13px">持久化到 .env 文件（重启后仍生效）</span>
            </n-space>
            <n-space style="margin-top:16px">
              <n-button type="primary" @click="saveSettings" :loading="savingSettings">保存参数</n-button>
              <n-button @click="resetToDefaults">恢复默认值</n-button>
            </n-space>
          </n-card>
        </n-space>
      </n-tab-pane>
    </n-tabs>

    <!-- ===== 分析详情弹窗 ===== -->
    <n-modal v-model:show="detailVisible" preset="card" title="分析详情" style="max-width:720px" :mask-closable="true">
      <n-spin :show="detailLoading">
        <template v-if="currentDetail">
          <!-- 基本信息 -->
          <n-descriptions label-placement="left" :column="2" size="small" bordered style="margin-bottom:16px">
            <n-descriptions-item label="模型">{{ currentDetail.model_name }}</n-descriptions-item>
            <n-descriptions-item label="状态">
              <n-tag :type="currentDetail.status === 'success' ? 'success' : 'error'" size="small">
                {{ currentDetail.status === 'success' ? '成功' : '失败' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="耗时">{{ formatMs(currentDetail.processing_time_ms) }}</n-descriptions-item>
            <n-descriptions-item label="时间">{{ formatDate(currentDetail.created_at || '') }}</n-descriptions-item>
            <n-descriptions-item v-if="currentDetail.error" label="错误信息" :span="2">
              <span style="color:red">{{ currentDetail.error }}</span>
            </n-descriptions-item>
          </n-descriptions>

          <!-- 提取的标签 -->
          <div v-if="currentDetail.tags.length > 0">
            <h4 style="margin-bottom:8px">提取的标签</h4>
            <n-space v-for="cat in ['style','item_type','color','fit','body_part','occasion','season','attribute']" :key="cat" style="margin-bottom:8px" align="center">
              <n-tag type="info" size="small" :bordered="false">{{ tagCategoryLabel[cat] || cat }}</n-tag>
              <template v-for="tag in currentDetail.tags.filter(t=>t.category===cat)" :key="tag.name">
                <n-tag size="small" round>
                  {{ tag.name }}
                  <span style="font-size:11px;color:#999;margin-left:2px">{{ tag.confidence }}</span>
                </n-tag>
              </template>
              <span v-if="!currentDetail.tags.some(t=>t.category===cat)" style="color:#ccc;font-size:12px">—</span>
            </n-space>
          </div>
          <n-empty v-else-if="!currentDetail.error" description="无标签数据" size="small" />

          <!-- 原始响应 -->
          <n-collapse v-if="currentDetail.raw_response" style="margin-top:16px">
            <n-collapse-item title="AI 原始响应" name="raw">
              <n-code :code="currentDetail.raw_response" language="json" word-wrap />
            </n-collapse-item>
          </n-collapse>
        </template>
      </n-spin>
    </n-modal>
  </div>
</template>

<style scoped>
.model-page { max-width: 1100px; margin: 0 auto; }
.model-page h2 { margin-bottom: 16px; }
</style>
