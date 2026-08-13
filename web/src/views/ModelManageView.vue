<script setup lang="ts">
/** AI 模型管理页：状态面板、模型列表、下载、分析队列、历史、参数调优。 */

import { h, ref, onMounted, onUnmounted, computed } from 'vue'
import { NTag, NButton, NPopconfirm, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'
import { useTagsStore } from '@/stores/tags'
import { useNotification } from '@/composables/useNotification'

const message = useMessage()
const { requestAndNotify, checkFailureAlert } = useNotification()

/** 复制文本到剪贴板（含降级方案） */
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  } catch {
    // 降级方案：使用 textarea + execCommand
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.cssText = 'position:fixed;left:-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      message.success('已复制到剪贴板')
    } catch {
      message.error('复制失败')
    }
  }
}
const tagsStore = useTagsStore()

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

// ===== 模型统计 =====
interface ModelStat {
  model_name: string; total_analyses: number; success_count: number
  failure_count: number; success_rate: number; avg_time_ms: number
  avg_tags: number; last_used: string
}
const modelStats = ref<ModelStat[]>([])
const totalAnalyses = ref(0)
const statsLoading = ref(false)

// ===== 单图测试 =====
const testInspirationId = ref('')
const testLoading = ref(false)
const testRawResponse = ref('')
const testParsed = ref<Record<string, any> | null>(null)
const testElapsedMs = ref(0)
const testModel = ref('')
const testCustomPrompt = ref('')

// ===== 分析队列 =====
interface QueueStats { total: number; analyzed: number; unanalyzed: number; failed: number }
interface ActiveAnalysis { active_analyses: Record<string, string>; count: number }
const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
const activeAnalyses = ref<Record<string, string>>({})
const batchAnalyzing = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

// ===== 队列可视化 =====
interface QueueItem {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  status: string
}
const pendingQueue = ref<QueueItem[]>([])
const queuePaused = ref(false)

async function loadPendingQueue() {
  try {
    const { data } = await apiClient.get<{ items: QueueItem[]; paused: boolean }>('/ai/queue/pending')
    pendingQueue.value = data.items
    queuePaused.value = data.paused
  } catch {}
}

async function cancelQueueItem(inspirationId: string) {
  try {
    await apiClient.delete(`/ai/queue/${inspirationId}`)
    message.success('已取消')
    loadPendingQueue()
    loadActiveAnalyses()
  } catch (e: any) {
    message.error(e.response?.data?.detail || e.response?.data?.message || '取消失败')
  }
}

async function togglePauseQueue() {
  try {
    if (queuePaused.value) {
      await apiClient.post('/ai/queue/resume')
      message.success('队列已恢复')
    } else {
      await apiClient.post('/ai/queue/pause')
      message.success('队列已暂停')
    }
    loadPendingQueue()
  } catch (e: any) {
    message.error('操作失败')
  }
}

// ===== 分析历史 =====
interface HistoryItem {
  id: number; inspiration_id: string; model_name: string
  thumbnail_path: string | null; file_path: string | null
  processing_time_ms: number | null; error: string | null
  status: string; created_at: string
  tags: Array<{ name: string; category: string }>
}
const history = ref<HistoryItem[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyPageSize = 20
const historyFilter = ref<string | null>(null)
const historyModelFilter = ref<string | null>(null)
const historySearchId = ref('')
const historyLoading = ref(false)
const selectedHistoryIds = ref<Set<number>>(new Set())
const historyModelNames = ref<string[]>([])

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

// ===== 分析结果对比 =====
interface CompareData {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  analyses: Array<{
    id: number
    model_name: string
    processing_time_ms: number | null
    error: string | null
    status: string
    created_at: string | null
    parsed_response: Record<string, any> | null
    tags_count: Record<string, number>
  }>
  analyses_count: number
  tag_diff: {
    added: string[]
    removed: string[]
    common: string[]
  } | null
  time_comparison: Array<{
    analysis_id: number
    model_name: string
    processing_time_ms: number | null
    created_at: string | null
  }>
}
const compareVisible = ref(false)
const compareLoading = ref(false)
const compareData = ref<CompareData | null>(null)

async function viewCompare(inspirationId: string) {
  compareVisible.value = true
  compareLoading.value = true
  compareData.value = null
  try {
    const { data } = await apiClient.get<CompareData>(`/ai/compare/${inspirationId}`)
    compareData.value = data
  } catch (e: any) {
    message.error(e.response?.data?.detail || '获取对比数据失败')
    compareVisible.value = false
  } finally {
    compareLoading.value = false
  }
}

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

// ===== Prompt 编辑 =====
const currentPrompt = ref('')
const editedPrompt = ref('')
const promptLoading = ref(false)
const promptSaving = ref(false)
const persistPrompt = ref(false)

// ===== Prompt 版本管理 =====
interface PromptVersion {
  prompt: string; saved_at: string; length: number;
}
const promptVersions = ref<PromptVersion[]>([])
const promptVersionsVisible = ref(false)

async function loadPromptVersions() {
  try {
    const { data } = await apiClient.get<{ versions: PromptVersion[]; current: string }>('/ai/prompt/versions')
    promptVersions.value = data.versions
  } catch { /* 静默 */ }
}

async function savePromptVersion() {
  try {
    // 先保存当前编辑中的 prompt（如果有修改）
    if (editedPrompt.value !== currentPrompt.value) {
      await apiClient.put('/ai/prompt', { prompt: editedPrompt.value, persist: false })
      currentPrompt.value = editedPrompt.value
    }
    await apiClient.post('/ai/prompt/save-version')
    message.success('版本已保存')
    loadPromptVersions()
  } catch { message.error('保存版本失败') }
}

async function rollbackPrompt(index: number) {
  try {
    const { data } = await apiClient.post('/ai/prompt/rollback', { index })
    editedPrompt.value = data.prompt
    currentPrompt.value = data.prompt
    message.success(data.message)
    loadPromptVersions()
  } catch (e: any) { message.error(e.response?.data?.detail || '回滚失败') }
}

// ===== 质量仪表盘 =====
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

// ===== 质量审核 =====
interface QualityReviewStats {
  total: number
  pending: number
  approved: number
  rejected: number
  pass_rate: number
  active: number
}
const qualityReviewStats = ref<QualityReviewStats | null>(null)
const qualityReviewLoading = ref(false)
const qualityReviewActive = ref<string[]>([])
const qualityChecking = ref(false)

async function loadQualityReview() {
  qualityReviewLoading.value = true
  try {
    const { data } = await apiClient.get<QualityReviewStats>('/ai/quality-stats')
    qualityReviewStats.value = data
    const active = await apiClient.get<{ active: string[]; count: number }>('/ai/quality-active')
    qualityReviewActive.value = active.data.active || []
  } catch { /* 静默 */ }
  finally { qualityReviewLoading.value = false }
}

async function triggerQualityCheck() {
  qualityChecking.value = true
  try {
    const { data } = await apiClient.post<{ message: string; count: number }>(
      '/ai/quality-check',
      null,
      { params: { limit: 200 } },
    )
    message.success(`已提交 ${data.count} 个素材进行审核`)
    loadQualityReview()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '审核提交失败')
  } finally {
    qualityChecking.value = false
  }
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

/** 卸载模型释放显存 */
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

// ===== 标签页 =====
const activeTab = ref('models')

onMounted(() => {
  refreshModels()
  loadGpuStats()
  loadQueue()
  loadHistory()
  loadModelNames()
  loadSettings()
  loadSamplingParams()
  loadPrompt()
  startPolling()
})

// 跟踪定时器引用用于 onUnmounted 清理
const timerRefs: ReturnType<typeof setTimeout>[] = []

onUnmounted(() => {
  stopPolling()
  cancelDownload()
  if (historyAbort) historyAbort.abort()
  timerRefs.forEach(clearTimeout)
  timerRefs.length = 0
})

// ---- 模型列表 ----
async function refreshModels() {
  statusLoading.value = true
  try {
    const { data } = await apiClient.get<ModelListResponse>('/ai/models')
    models.value = data.models
    activeModel.value = data.active_model
    aiSettings.value.active_model = data.active_model // 同步设置面板
    ollamaConnected.value = true
  } catch {
    ollamaConnected.value = false
  } finally {
    statusLoading.value = false
  }
}

async function setActiveModel(name: string) {
  const previous = activeModel.value
  try {
    await apiClient.put('/ai/models/active', null, { params: { model_name: name } })
    activeModel.value = name
    aiSettings.value.active_model = name
    message.success(`已切换到 ${name}`)
    refreshModels()
  } catch (e: any) {
    aiSettings.value.active_model = previous // 失败回滚下拉框
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
let downloadAbortController: AbortController | null = null

async function startDownload() {
  const name = downloadName.value.trim()
  if (!name) return
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
      // 按 SSE 帧分割
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
    // 5 秒后自动清空进度提示
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

// ---- 分析队列 ----
async function loadQueue() {
  try {
    const { data } = await apiClient.get<QueueStats>('/ai/queue')
    queueStats.value = data
    checkFailureAlert(data.failed, data.total)
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
  scheduleNextPoll()
}

function scheduleNextPoll() {
  const wasActive = Object.keys(activeAnalyses.value).length > 0
  const interval = wasActive ? 3000 : 15000
  pollTimer = setTimeout(async () => {
    await loadActiveAnalyses()
    loadPendingQueue()
    const isActive = Object.keys(activeAnalyses.value).length > 0
    // 始终刷新队列统计（轻量查询），有活跃时或刚变空闲时刷新历史
    loadQueue()
    if (isActive || wasActive) {
      loadHistory()
    }
    // 质量审核：仅在该标签激活时刷新
    if (activeTab.value === 'review') {
      loadQualityReview()
    }
    if (pollTimer !== null) scheduleNextPoll()
  }, interval)
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

async function triggerBatchAnalyze() {
  batchAnalyzing.value = true
  try {
    const { data } = await apiClient.get<{ ids: string[]; count: number }>('/ai/unanalyzed-ids')
    if (data.count === 0) {
      message.info('所有素材均已分析过，无需重复分析')
      return
    }
    await apiClient.post('/ai/batch-analyze', data.ids)
    message.success(`已将 ${data.count} 个素材加入分析队列`)
    requestAndNotify('批量分析已启动', { body: `${data.count} 个素材已加入分析队列`, tag: 'batch-analyze' })
    loadQueue(); loadHistory(); loadActiveAnalyses()
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
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重试失败')
  }
}

// ---- 分析历史 ----
let historyAbort: AbortController | null = null

async function loadHistory() {
  if (historyAbort) historyAbort.abort()
  historyAbort = new AbortController()
  historyLoading.value = true
  try {
    const params: any = { page: historyPage.value, size: historyPageSize }
    if (historyFilter.value) params.status = historyFilter.value
    if (historyModelFilter.value) params.model_name = historyModelFilter.value
    if (historySearchId.value.trim()) params.inspiration_id = historySearchId.value.trim()
    const { data } = await apiClient.get('/ai/history', { params, signal: historyAbort.signal })
    history.value = data.items
    historyTotal.value = data.total
  } catch (e: any) {
    if (e?.code !== 'ERR_CANCELED') message.error('加载历史失败')
  } finally {
    historyLoading.value = false
  }
}

function filterHistory(status: string | null) {
  historyFilter.value = status
  historyPage.value = 1
  selectedHistoryIds.value = new Set()
  loadHistory()
}

function filterByModel(model: string | null) {
  historyModelFilter.value = model
  historyPage.value = 1
  selectedHistoryIds.value = new Set()
  loadHistory()
}

function searchById() {
  historyPage.value = 1
  selectedHistoryIds.value = new Set()
  loadHistory()
}

/** 加载历史中出现过的模型名称列表 */
async function loadModelNames() {
  try {
    const { data } = await apiClient.get<{ models: string[] }>('/ai/history/model-names')
    historyModelNames.value = data.models
  } catch { /* 静默 */ }
}

/** 切换单条选中 */
function toggleSelectHistory(logId: number) {
  const next = new Set(selectedHistoryIds.value)
  if (next.has(logId)) next.delete(logId)
  else next.add(logId)
  selectedHistoryIds.value = next
}

/** 全选/取消全选当前页 */
function selectAllHistory() {
  if (selectedHistoryIds.value.size === history.value.length && history.value.length > 0) {
    selectedHistoryIds.value = new Set()
  } else {
    selectedHistoryIds.value = new Set(history.value.map(h => h.id))
  }
}

/** 批量删除选中记录 */
async function batchDeleteHistory() {
  if (selectedHistoryIds.value.size === 0) return
  try {
    await apiClient.post('/ai/history/batch-delete', { ids: [...selectedHistoryIds.value] })
    message.success(`已删除 ${selectedHistoryIds.value.size} 条记录`)
    selectedHistoryIds.value = new Set()
    loadHistory()
    loadQueue()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '批量删除失败')
  }
}

/** 批量重试选中记录（根据素材ID去重） */
async function batchRetryHistory() {
  if (selectedHistoryIds.value.size === 0) return
  try {
    const { data } = await apiClient.post('/ai/history/batch-retry', { ids: [...selectedHistoryIds.value] })
    message.success(data.message)
    requestAndNotify('批量重试已启动', { body: data.message, tag: 'batch-retry' })
    selectedHistoryIds.value = new Set()
    loadHistory()
    loadActiveAnalyses()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '批量重试失败')
  }
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
const retryingAll = ref(false)

async function retryAllFailed() {
  retryingAll.value = true
  try {
    const { data } = await apiClient.post('/ai/retry-all-failed')
    message.success(data.message || '已加入重试队列')
    requestAndNotify('失败重试已启动', { body: data.message, tag: 'retry-failed' })
    loadQueue(); loadHistory(); loadActiveAnalyses()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重试失败')
  } finally {
    retryingAll.value = false
  }
}

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
  body_part: '穿着方式', occasion: '场合', attribute: '属性',
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

// ---- Prompt 管理 ----
async function loadPrompt() {
  promptLoading.value = true
  try {
    const { data } = await apiClient.get<{ prompt: string; length: number }>('/ai/prompt')
    currentPrompt.value = data.prompt
    editedPrompt.value = data.prompt
  } catch { /* 忽略 */ }
  finally { promptLoading.value = false }
}

async function savePrompt() {
  promptSaving.value = true
  try {
    await apiClient.put('/ai/prompt', {
      prompt: editedPrompt.value,
      persist: persistPrompt.value,
    })
    currentPrompt.value = editedPrompt.value
    message.success('Prompt 已更新' + (persistPrompt.value ? '并持久化' : ''))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存 Prompt 失败')
  } finally { promptSaving.value = false }
}

function resetPrompt() {
  editedPrompt.value = currentPrompt.value
  message.info('已恢复为上次保存的 Prompt')
}

// ===== 重置所有数据 =====
const resetStep = ref(0) // 0=idle, 1=一次确认, 2=二次确认
const resetting = ref(false)

function startReset() { resetStep.value = 1 }
function cancelReset() { resetStep.value = 0 }

async function confirmResetStep() {
  if (resetStep.value === 1) {
    resetStep.value = 2 // 进入二次确认
  } else if (resetStep.value === 2) {
    resetStep.value = 0
    resetting.value = true
    try {
      const { data } = await apiClient.delete('/ai/reset', { params: { confirm: 'yes' } })
      message.success(data.message || '所有数据已重置')
      // 刷新所有状态
      refreshModels(); loadQueue(); loadHistory(); loadSettings()
      tagsStore.load(true)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '重置失败')
    } finally {
      resetting.value = false
    }
  }
}

// ---- 模型统计 ----
async function loadModelStats() {
  statsLoading.value = true
  try {
    const { data } = await apiClient.get<{ models: ModelStat[]; total_analyses: number }>('/ai/model-stats')
    modelStats.value = data.models
    totalAnalyses.value = data.total_analyses
  } catch { /* 忽略 */ }
  finally { statsLoading.value = false }
}

// ---- 单图测试 ----
async function testAnalyze() {
  if (!testInspirationId.value.trim()) return
  testLoading.value = true
  testRawResponse.value = ''
  testParsed.value = null
  testElapsedMs.value = 0
  testModel.value = ''

  try {
    const baseUrl = apiClient.defaults.baseURL || '/api'
    const params = new URLSearchParams({ inspiration_id: testInspirationId.value.trim() })
    if (testCustomPrompt.value.trim()) params.set('custom_prompt', testCustomPrompt.value.trim())

    const response = await fetch(`${baseUrl}/ai/test-analyze?${params}`, { method: 'POST' })
    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || '测试请求失败')
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
            if (data.type === 'done') {
              testRawResponse.value = data.raw_response || ''
              testParsed.value = data.parsed || {}
              testElapsedMs.value = data.elapsed_ms || 0
              testModel.value = data.model || ''
              message.success(`测试完成 (${data.elapsed_ms}ms)`)
            } else if (data.type === 'error') {
              message.error(data.message || '测试失败')
            }
          } catch {}
        }
      }
    }
  } catch (e: any) {
    message.error(e.message || '测试请求中断')
  } finally {
    testLoading.value = false
  }
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

    <n-tabs v-model:value="activeTab" type="line" @update:value="(v: string) => { if (v === 'quality') loadQuality(); if (v === 'review') loadQualityReview() }">
      <!-- ===== Tab: 模型 ===== -->
      <n-tab-pane name="models" tab="模型管理">
        <!-- 连接状态 -->
        <n-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom:16px">
          {{ ollamaConnected ? `Ollama 已连接 · 活跃模型: ${activeModel}` : 'Ollama 未连接' }}
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
          <!-- 已加载模型列表 -->
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
              { title: '状态', key: 'loaded', width: 80, render: (row: OllamaModel) => row.is_active ? h(NTag, {type:'success',size:'small'}, '活跃') : row.loaded ? h(NTag, {type:'info',size:'small'}, '已加载') : h(NTag, {size:'small'}, '休眠') },
              { title: '更新时间', key: 'modified', width: 160, render: (row: OllamaModel) => row.modified?.split('T')[0] },
              { title: '操作', key: 'actions', render: (row: OllamaModel) => h('span', {style:'display:flex;gap:6px'}, [
                !row.is_active ? h(NButton, {size:'tiny',onClick:()=>setActiveModel(row.name)}, '启用') : null,
                !row.is_active ? h(NPopconfirm, {onPositiveClick:()=>deleteModel(row.name)},
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
      </n-tab-pane>

      <!-- ===== Tab: 标签分析 ===== -->
      <n-tab-pane name="queue" tab="标签分析">
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

        <!-- 正在分析提示 + 暂停/恢复 -->
        <div class="queue-controls" style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap">
          <n-alert v-if="Object.keys(activeAnalyses).length > 0" type="info" style="flex:1;min-width:300px" closable>
            <template #header>正在分析 {{ Object.keys(activeAnalyses).length }} 个素材...</template>
            <div v-for="(status, id) in activeAnalyses" :key="id" style="font-size:12px;color:#666">
              素材 {{ id.slice(0, 8) }}... — {{ status }}
            </div>
          </n-alert>
          <n-button
            v-if="Object.keys(activeAnalyses).length > 0 || pendingQueue.length > 0"
            :type="queuePaused ? 'success' : 'warning'"
            size="small"
            @click="togglePauseQueue"
          >
            {{ queuePaused ? '▶ 恢复队列' : '⏸ 暂停队列' }}
          </n-button>
        </div>

        <!-- 排队中素材缩略图 -->
        <div v-if="pendingQueue.length > 0" class="pending-queue">
          <div style="font-size:13px;font-weight:600;margin-bottom:8px">
            📋 排队中 ({{ pendingQueue.length }})
            <span v-if="queuePaused" style="color:#f0a020;font-size:12px"> — 已暂停</span>
          </div>
          <div class="pending-grid">
            <div v-for="item in pendingQueue" :key="item.inspiration_id" class="pending-card">
              <img
                v-if="item.thumbnail_path"
                :src="getFileUrl(item.thumbnail_path)"
                style="width:80px;height:120px;object-fit:cover;border-radius:4px"
              />
              <img
                v-else-if="item.file_path"
                :src="getFileUrl(item.file_path)"
                style="width:80px;height:120px;object-fit:cover;border-radius:4px"
              />
              <div style="font-size:10px;color:#999;text-align:center;margin-top:2px">
                {{ item.inspiration_id.slice(0, 6) }}...
              </div>
              <div style="font-size:10px;color:#666;text-align:center">{{ item.status }}</div>
              <n-button
                v-if="item.status === '排队中'"
                size="tiny"
                type="error"
                ghost
                style="margin-top:2px;font-size:10px"
                @click="cancelQueueItem(item.inspiration_id)"
              >
                取消
              </n-button>
            </div>
          </div>
        </div>

        <!-- 分析历史 -->
        <n-card title="分析历史" size="small">
          <template #header-extra>
            <n-space :size="8">
              <n-button size="small" type="warning" secondary :loading="retryingAll" @click="retryAllFailed">
                一键重试失败 {{ queueStats.failed > 0 ? `(${queueStats.failed})` : '' }}
              </n-button>
              <n-popconfirm @positive-click="deleteAllFailed">
                <template #trigger>
                  <n-button size="small" type="error" secondary :loading="clearingFailed">
                    删除所有失败记录
                  </n-button>
                </template>
                确定要删除所有失败记录吗？此操作不可恢复。
              </n-popconfirm>
              <n-button size="small" @click="loadHistory" :loading="historyLoading">刷新</n-button>
            </n-space>
          </template>

          <!-- 筛选栏 -->
          <div class="history-filters">
            <n-radio-group v-model:value="historyFilter" @update:value="filterHistory" size="small">
              <n-radio-button :value="null">全部</n-radio-button>
              <n-radio-button value="success">成功</n-radio-button>
              <n-radio-button value="error">失败</n-radio-button>
            </n-radio-group>
            <n-select
              v-if="historyModelNames.length"
              v-model:value="historyModelFilter"
              :options="[{label:'全部模型',value:null},...historyModelNames.map(m=>({label:m,value:m}))]"
              size="small"
              style="width:160px"
              @update:value="filterByModel"
              placeholder="按模型筛选"
            />
            <n-input
              v-model:value="historySearchId"
              size="small"
              placeholder="搜索素材 ID..."
              style="width:200px"
              clearable
              @keyup.enter="searchById"
              @clear="searchById"
            >
              <template #suffix>
                <n-button size="tiny" @click="searchById">🔍</n-button>
              </template>
            </n-input>
          </div>

          <!-- 批量操作栏 -->
          <div v-if="selectedHistoryIds.size > 0" class="batch-bar">
            <span>已选 {{ selectedHistoryIds.size }} 条</span>
            <n-button size="tiny" type="primary" ghost @click="batchRetryHistory">重新分析</n-button>
            <n-popconfirm @positive-click="batchDeleteHistory">
              <template #trigger>
                <n-button size="tiny" type="error" ghost>批量删除</n-button>
              </template>
              确定删除选中的 {{ selectedHistoryIds.size }} 条记录？
            </n-popconfirm>
            <n-button size="tiny" @click="selectedHistoryIds = new Set()">取消选择</n-button>
          </div>

          <n-data-table
            v-if="history.length"
            :columns="[
              { title: () => h('input', { type:'checkbox', checked: selectedHistoryIds.size === history.length && history.length > 0, onClick: selectAllHistory }), key:'_check', width: 36, render: (row: HistoryItem) => h('input', { type:'checkbox', checked: selectedHistoryIds.has(row.id), onClick: () => toggleSelectHistory(row.id) }) },
              { title: '预览', key: 'thumbnail', width: 70, render: (row: HistoryItem) => row.thumbnail_path ? h('img', {src:getFileUrl(row.thumbnail_path), style:'width:48px;height:72px;object-fit:cover;border-radius:4px'}) : '-' },
              { title: '模型', key: 'model_name', width: 130 },
              { title: '状态', key: 'status', width: 70, render: (row: HistoryItem) => h(NTag, {type:row.status==='success'?'success':'error',size:'small'}, row.status==='success'?'成功':'失败') },
              { title: '提取标签', key: 'tags', width: 180, render: (row: HistoryItem) => {
                const tags = row.tags || []
                if (tags.length === 0) return '-'
                const shown = tags.slice(0, 4)
                const more = tags.length > 4 ? ` +${tags.length - 4}` : ''
                return h('span', {style:'display:flex;flex-wrap:wrap;gap:2px'}, [
                  ...shown.map(t => h(NTag, {key:t.name,size:'tiny',bordered:false}, t.name)),
                  more ? h('span', {style:'font-size:11px;color:#999'}, more) : null,
                ])
              }},
              { title: '失败原因', key: 'error', width: 180, render: (row: HistoryItem) => row.error
                ? h('span', {
                    title: row.error,
                    style:'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px;cursor:pointer;text-decoration:underline;text-underline-offset:2px',
                    onClick: () => copyText(row.error!)
                  }, row.error)
                : h('span', {style:'font-size:12px;color:#999'}, '-') },
              { title: '耗时', key: 'time', width: 80, render: (row: HistoryItem) => formatMs(row.processing_time_ms) },
              { title: '时间', key: 'created_at', width: 160, render: (row: HistoryItem) => formatDate(row.created_at) },
              { title: '操作', key: 'actions', width: 140, render: (row: HistoryItem) => h('span', {style:'display:flex;gap:4px'}, [
                row.status === 'success' ? h(NButton, {size:'tiny',onClick:()=>viewDetail(row.id)}, '详情') : null,
                h(NButton, {size:'tiny',onClick:()=>viewCompare(row.inspiration_id)}, '对比'),
                row.status === 'error' ? h(NButton, {size:'tiny',onClick:()=>retryAnalysis(row.inspiration_id)}, '重试') : null,
                h(NPopconfirm, {onPositiveClick:()=>deleteLog(row.id)},
                  { trigger: ()=>h(NButton,{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此记录？' }
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

          <!-- Prompt 编辑 -->
          <n-card title="分析 Prompt" size="small">
            <n-spin :show="promptLoading">
              <n-input
                v-model:value="editedPrompt"
                type="textarea"
                :autosize="{ minRows: 8, maxRows: 20 }"
                placeholder="输入 AI 分析 prompt..."
                style="font-family:monospace;font-size:13px"
              />
              <div style="display:flex;align-items:center;justify-content:space-between;margin-top:12px">
                <n-space align="center">
                  <n-button type="primary" size="small" @click="savePrompt" :loading="promptSaving">保存 Prompt</n-button>
                  <n-button size="small" @click="resetPrompt">撤销修改</n-button>
                </n-space>
                <n-space align="center">
                  <n-switch v-model:value="persistPrompt" size="small" />
                  <span style="font-size:12px;color:#999">持久化保存</span>
                </n-space>
              </div>
              <p style="font-size:11px;color:#999;margin-top:8px">
                修改 prompt 会影响后续所有 AI 分析结果。改动后建议先用「单图测试」验证效果。
              </p>

              <!-- Prompt 版本管理 -->
              <n-button size="tiny" style="margin-top:8px" @click="savePromptVersion(); promptVersionsVisible = true; loadPromptVersions()">保存版本</n-button>
              <n-button size="tiny" style="margin-top:8px;margin-left:6px" @click="promptVersionsVisible = !promptVersionsVisible; loadPromptVersions()">
                {{ promptVersionsVisible ? '隐藏历史' : '版本历史' }} {{ promptVersions.length > 0 ? `(${promptVersions.length})` : '' }}
              </n-button>

              <div v-if="promptVersionsVisible && promptVersions.length > 0" class="prompt-versions" style="margin-top:8px;max-height:200px;overflow-y:auto">
                <div v-for="(v, i) in promptVersions" :key="i" style="display:flex;align-items:center;justify-content:space-between;padding:4px 8px;background:#f5f5f5;border-radius:4px;margin-bottom:4px;font-size:12px">
                  <span>版本 #{{ promptVersions.length - i }} — {{ v.saved_at?.split('T')[0] }} {{ v.saved_at?.split('T')[1]?.slice(0,5) }} ({{ v.length }} 字符)</span>
                  <n-button size="tiny" @click="rollbackPrompt(i)">回滚</n-button>
                </div>
              </div>
              <div v-else-if="promptVersionsVisible" style="font-size:12px;color:#999;margin-top:4px">暂无版本历史，修改 prompt 后点击「保存版本」创建</div>
            </n-spin>
          </n-card>

          <!-- 单图测试 -->
          <n-card title="单图即时测试" size="small">
            <p style="font-size:12px;color:#999;margin-bottom:12px">
              使用当前 prompt 和参数对单张图片进行测试分析，不保存记录，不影响正式数据。
            </p>
            <n-space align="center" style="margin-bottom:12px">
              <n-input
                v-model:value="testInspirationId"
                placeholder="输入素材 ID 或完整 UUID"
                style="width:280px"
                size="small"
              />
              <n-button
                type="primary"
                size="small"
                @click="testAnalyze"
                :loading="testLoading"
                :disabled="!testInspirationId.trim()"
              >
                {{ testLoading ? '分析中...' : '开始测试' }}
              </n-button>
            </n-space>
            <!-- 自定义 prompt（可选覆盖） -->
            <n-input
              v-model:value="testCustomPrompt"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 6 }"
              placeholder="可选：临时覆盖 prompt（留空则使用上方保存的 prompt）"
              size="small"
              style="font-family:monospace;font-size:12px;margin-bottom:12px"
            />
            <!-- 结果展示 -->
            <div v-if="testRawResponse || testLoading" style="margin-top:8px">
              <n-alert v-if="testModel" type="success" style="margin-bottom:8px">
                <template #header>
                  测试完成 — 模型: {{ testModel }} · 耗时: {{ formatMs(testElapsedMs) }}
                </template>
              </n-alert>
              <n-collapse>
                <n-collapse-item title="解析结果" name="parsed">
                  <div v-if="testParsed && Object.keys(testParsed).length">
                    <div v-for="(val, key) in testParsed" :key="key" style="margin-bottom:6px">
                      <n-tag type="info" size="tiny" style="margin-right:4px">{{ key }}</n-tag>
                      <span style="font-size:12px;word-break:break-all">{{ JSON.stringify(val) }}</span>
                    </div>
                  </div>
                  <n-empty v-else description="未能解析出结构化结果" size="small" />
                </n-collapse-item>
                <n-collapse-item title="原始响应" name="raw">
                  <n-code :code="testRawResponse" language="json" word-wrap />
                </n-collapse-item>
              </n-collapse>
            </div>
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

          <!-- 危险操作：重置所有数据 -->
          <n-card title="⚠ 危险操作" size="small" style="border-color:#ef4444">
            <p style="font-size:13px;color:#999;margin-bottom:12px">
              删除数据库中所有素材、标签、分析记录，并清空所有照片文件。此操作不可恢复！
            </p>

            <!-- 阶段 0: 初始按钮 -->
            <n-button v-if="resetStep === 0" type="error" @click="startReset">
              重置所有数据
            </n-button>

            <!-- 阶段 1: 第一次确认 -->
            <n-popconfirm
              v-if="resetStep === 1"
              @positive-click="confirmResetStep"
              @negative-click="cancelReset"
            >
              <template #trigger>
                <n-button type="error" :loading="resetting">
                  第一次确认：确定要删除所有数据吗？
                </n-button>
              </template>
              此操作将清空数据库和所有照片文件！请再次确认。
            </n-popconfirm>

            <!-- 阶段 2: 第二次确认 -->
            <n-popconfirm
              v-if="resetStep === 2"
              @positive-click="confirmResetStep"
              @negative-click="cancelReset"
            >
              <template #trigger>
                <n-button type="error" secondary :loading="resetting">
                  第二次确认：真的要删除吗？此操作不可恢复！
                </n-button>
              </template>
              最后一次确认：点击"确定"后将立即删除所有数据！
            </n-popconfirm>

            <p v-if="resetting" style="font-size:12px;color:#ef4444;margin-top:8px">
              正在删除所有数据...
            </p>
          </n-card>
        </n-space>
      </n-tab-pane>

      <!-- ===== Tab: 分析质量仪表盘 ===== -->
      <n-tab-pane name="quality" tab="分析质量">
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
      </n-tab-pane>

      <!-- ===== Tab: 质量审核 ===== -->
      <n-tab-pane name="review" tab="质量审核">
        <n-spin :show="qualityReviewLoading">
          <template v-if="qualityReviewStats">
            <!-- 统计卡片 -->
            <n-grid :cols="4" :x-gap="12" style="margin-bottom:16px">
              <n-gi><n-card size="small"><n-statistic label="待审核" :value="qualityReviewStats.pending" /></n-card></n-gi>
              <n-gi><n-card size="small"><n-statistic label="已通过" :value="qualityReviewStats.approved" /></n-card></n-gi>
              <n-gi><n-card size="small"><n-statistic label="已拒绝" :value="qualityReviewStats.rejected" /></n-card></n-gi>
              <n-gi><n-card size="small"><n-statistic label="通过率" :value="`${qualityReviewStats.pass_rate}%`" /></n-card></n-gi>
            </n-grid>

            <!-- 进度条 + 审核操作 -->
            <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
              <n-progress
                v-if="qualityReviewStats.total > 0"
                type="line"
                :percentage="Math.round((qualityReviewStats.approved + qualityReviewStats.rejected) / qualityReviewStats.total * 100)"
                :height="24"
                style="flex:1"
              />
              <n-button
                type="primary"
                :loading="qualityChecking"
                :disabled="qualityReviewStats.pending === 0"
                @click="triggerQualityCheck"
              >
                {{ qualityReviewStats.pending > 0 ? `审核全部待审核 (${qualityReviewStats.pending})` : '全部已审核' }}
              </n-button>
            </div>

            <!-- 正在审核提示 -->
            <n-alert v-if="qualityReviewActive.length > 0" type="info" style="margin-bottom:16px">
              <template #header>正在审核 {{ qualityReviewActive.length }} 个素材...</template>
              <div style="font-size:12px;color:#666">
                {{ qualityReviewActive.map((id) => id.slice(0, 8) + '...').join('、') }}
              </div>
            </n-alert>

            <!-- 结果查看说明 -->
            <n-alert type="info" style="margin-bottom:16px">
              <template #header>💡 如何查看审核结果</template>
              审核结果已写入素材的审核状态。前往「素材库」页，用筛选栏的「待审核 / 已通过 / 已拒绝」查看，并对误判的图片点击卡片上的 ✓ 翻案。
            </n-alert>
          </template>
          <n-empty v-else-if="!qualityReviewLoading" description="点击加载审核数据" size="small">
            <template #extra><n-button size="small" @click="loadQualityReview">加载</n-button></template>
          </n-empty>
        </n-spin>
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
            <n-space v-for="cat in ['style','item_type','color','fit','body_part','occasion','attribute']" :key="cat" style="margin-bottom:8px" align="center">
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

    <!-- ===== 分析结果对比弹窗 ===== -->
    <n-modal v-model:show="compareVisible" preset="card" title="分析结果对比" style="max-width:960px" :mask-closable="true">
      <n-spin :show="compareLoading">
        <template v-if="compareData">
          <!-- 素材预览 -->
          <div v-if="compareData.thumbnail_path" style="text-align:center;margin-bottom:16px">
            <img :src="getFileUrl(compareData.thumbnail_path)" style="max-height:200px;border-radius:8px" />
          </div>

          <!-- 耗时对比 -->
          <n-card title="⏱ 耗时对比" size="small" style="margin-bottom:12px">
            <div style="display:flex;gap:12px;flex-wrap:wrap">
              <div v-for="tc in compareData.time_comparison" :key="tc.analysis_id"
                style="flex:1;min-width:140px;text-align:center;padding:8px;background:#f5f5f5;border-radius:6px">
                <div style="font-weight:600;font-size:13px">{{ tc.model_name }}</div>
                <div style="font-size:11px;color:#999">{{ formatDate(tc.created_at) }}</div>
                <n-tag :type="tc.processing_time_ms ? 'success' : 'default'" size="small" style="margin-top:4px">
                  {{ formatMs(tc.processing_time_ms) }}
                </n-tag>
              </div>
            </div>
          </n-card>

          <!-- 标签数量对比 -->
          <n-card title="📊 标签数量对比" size="small" style="margin-bottom:12px">
            <div style="display:flex;gap:12px;flex-wrap:wrap">
              <div v-for="a in compareData.analyses" :key="'count-'+a.id"
                style="flex:1;min-width:120px;text-align:center;padding:8px;background:#f5f5f5;border-radius:6px">
                <div style="font-size:11px;color:#999">{{ a.model_name }}</div>
                <div v-for="(count, cat) in a.tags_count" :key="cat" style="font-size:12px;margin:2px 0">
                  <n-tag size="tiny" :bordered="false">{{ cat }}</n-tag> {{ count }}
                </div>
              </div>
            </div>
          </n-card>

          <!-- 标签差异 (首次 vs 末次) -->
          <n-card v-if="compareData.tag_diff" title="🔄 标签差异（首次 → 末次）" size="small" style="margin-bottom:12px">
            <div v-if="compareData.tag_diff.added.length" style="margin-bottom:8px">
              <span style="color:#18a058;font-weight:600;font-size:12px">+ 新增 ({{ compareData.tag_diff.added.length }}):</span>
              <n-tag v-for="t in compareData.tag_diff.added" :key="'a-'+t" size="tiny" type="success" style="margin:1px">{{ t }}</n-tag>
            </div>
            <div v-if="compareData.tag_diff.removed.length" style="margin-bottom:8px">
              <span style="color:#d03050;font-weight:600;font-size:12px">− 消失 ({{ compareData.tag_diff.removed.length }}):</span>
              <n-tag v-for="t in compareData.tag_diff.removed" :key="'r-'+t" size="tiny" type="error" style="margin:1px">{{ t }}</n-tag>
            </div>
            <div v-if="compareData.tag_diff.common.length">
              <span style="color:#2080f0;font-weight:600;font-size:12px">= 共同 ({{ compareData.tag_diff.common.length }}):</span>
              <n-tag v-for="t in compareData.tag_diff.common.slice(0, 20)" :key="'c-'+t" size="tiny" type="info" style="margin:1px">{{ t }}</n-tag>
              <span v-if="compareData.tag_diff.common.length > 20" style="font-size:11px;color:#999"> ...还有 {{ compareData.tag_diff.common.length - 20 }} 个</span>
            </div>
          </n-card>

          <!-- 各次分析详情 -->
          <n-collapse>
            <n-collapse-item v-for="(a, idx) in compareData.analyses" :key="a.id"
              :title="`#${idx + 1} — ${a.model_name} — ${a.status === 'success' ? '✓' : '✗'} — ${formatDate(a.created_at)}`"
              :name="String(a.id)">
              <div v-if="a.error" style="color:#d03050;font-size:13px;margin-bottom:8px">{{ a.error }}</div>
              <div v-if="a.parsed_response" style="font-size:12px">
                <div v-for="(val, key) in a.parsed_response" :key="key" style="margin:4px 0">
                  <n-tag type="info" size="tiny">{{ key }}</n-tag>
                  <code style="margin-left:4px;word-break:break-all">{{ JSON.stringify(val) }}</code>
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>

          <n-empty v-if="compareData.analyses.length < 2" description="只有一次分析记录，无法对比" size="small" style="margin-top:16px" />
        </template>
      </n-spin>
    </n-modal>
  </div>
</template>

<style scoped>
.model-page { max-width: 1100px; margin: 0 auto; }
.model-page h2 { margin-bottom: 16px; }

/* 历史记录筛选栏 */
.history-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

/* 批量操作栏 */
/* 排队中缩略图网格 */
.pending-queue {
  margin-bottom: 16px;
}
.pending-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pending-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fafafa;
}

/* 每日趋势图 */
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

.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f0f7ff;
  border: 1px solid #d0e3ff;
  border-radius: 6px;
  font-size: 13px;
}
</style>
