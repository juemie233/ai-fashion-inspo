<script setup lang="ts">
/** 标签分析面板：分析队列、历史记录、分析详情与结果对比。 */

import { h, ref, onMounted, onUnmounted } from 'vue'
import { NTag, NButton, NPopconfirm, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'
import { useNotification } from '@/composables/useNotification'
import { formatMs, formatDate } from '@/utils/format'

const message = useMessage()
const { requestAndNotify, checkFailureAlert } = useNotification()

/** 复制文本到剪贴板 */
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  } catch {
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

// ===== 分析队列 =====
interface QueueStats { total: number; analyzed: number; unanalyzed: number; failed: number }
interface ActiveAnalysis { active_analyses: Record<string, string>; count: number }
const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
const activeAnalyses = ref<Record<string, string>>({})
const batchAnalyzing = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null

// ===== 批量分析任务（数据库驱动任务队列，轮询进度） =====
interface TaskInfo {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: { success_count?: number; failed_count?: number } | null
  error: string | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  created_at: string
  updated_at: string
}
const batchTask = ref<TaskInfo | null>(null)
let batchPollTimer: ReturnType<typeof setTimeout> | null = null

/** 任务状态中文标签 */
const taskStatusLabel: Record<string, string> = {
  pending: '排队中',
  running: '进行中',
  success: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

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
    loadQueue()
    if (isActive || wasActive) {
      loadHistory()
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
    // 创建批量分析任务，立即拿到 task_id，后续轮询任务状态
    const { data: created } = await apiClient.post<{ task_id: number; message: string; count: number; skipped: number }>('/ai/batch-analyze', data.ids)
    batchTask.value = {
      id: created.task_id,
      type: 'batch_analyze',
      status: 'pending',
      progress: 0,
      total: created.count,
      done: 0,
      result: null,
      error: null,
      retry_count: 0,
      max_retries: 2,
      next_retry_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    message.success(`已创建批量分析任务 #${created.task_id}，共 ${created.count} 个素材`)
    requestAndNotify('批量分析已创建', { body: `任务 #${created.task_id}，${created.count} 个素材已加入队列`, tag: 'batch-analyze' })
    startBatchPolling(created.task_id)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '批量分析失败')
  } finally {
    batchAnalyzing.value = false
  }
}

/** 轮询批量分析任务状态（约 1 秒一次），完成后刷新分析结果 */
function startBatchPolling(taskId: number) {
  stopBatchPolling()
  const poll = async () => {
    try {
      const { data } = await apiClient.get<TaskInfo>(`/tasks/${taskId}`)
      batchTask.value = data
      if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
        stopBatchPolling()
        if (data.status === 'success') {
          const successCount = data.result?.success_count
          const failedCount = data.result?.failed_count
          const detail = (successCount !== undefined && failedCount !== undefined)
            ? `成功 ${successCount}，失败 ${failedCount}`
            : '已完成'
          message.success(`批量分析完成：${detail}`)
        } else if (data.status === 'failed') {
          message.error(`批量分析失败：${data.error || '未知错误'}`)
        } else {
          message.info('批量分析任务已取消')
        }
        loadQueue(); loadHistory(); loadActiveAnalyses()
        return
      }
      batchPollTimer = setTimeout(poll, 1000)
    } catch (e: any) {
      stopBatchPolling()
      message.error('获取任务状态失败，请稍后手动刷新')
    }
  }
  poll()
}

/** 取消排队中的批量分析任务 */
async function cancelBatchTask() {
  if (!batchTask.value) return
  try {
    await apiClient.post(`/tasks/${batchTask.value.id}/cancel`)
    message.success('任务已取消')
    stopBatchPolling()
    batchTask.value = { ...batchTask.value, status: 'cancelled' }
    loadQueue()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '取消失败')
  }
}

function stopBatchPolling() {
  if (batchPollTimer) { clearTimeout(batchPollTimer); batchPollTimer = null }
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

// ===== 分析历史 =====
interface HistoryItem {
  id: number; inspiration_id: string; model_name: string
  log_type?: string
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
let historyAbort: AbortController | null = null
let historySeq = 0  // 请求序号，防止取消竞态导致 loading 提前熄灭

async function loadHistory() {
  if (historyAbort) historyAbort.abort()
  historyAbort = new AbortController()
  historyLoading.value = true
  const seq = ++historySeq
  try {
    const params: any = { page: historyPage.value, size: historyPageSize }
    if (historyFilter.value) params.status = historyFilter.value
    if (historyModelFilter.value) params.model_name = historyModelFilter.value
    if (historySearchId.value.trim()) params.inspiration_id = historySearchId.value.trim()
    const { data } = await apiClient.get('/ai/history', { params, signal: historyAbort.signal })
    if (seq !== historySeq) return
    history.value = data.items
    historyTotal.value = data.total
  } catch (e: any) {
    if (e?.code !== 'ERR_CANCELED') message.error('加载历史失败')
  } finally {
    if (seq === historySeq) historyLoading.value = false
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

async function loadModelNames() {
  try {
    const { data } = await apiClient.get<{ models: string[] }>('/ai/history/model-names')
    historyModelNames.value = data.models
  } catch { /* 静默 */ }
}

function toggleSelectHistory(logId: number) {
  const next = new Set(selectedHistoryIds.value)
  if (next.has(logId)) next.delete(logId)
  else next.add(logId)
  selectedHistoryIds.value = next
}

function selectAllHistory() {
  if (selectedHistoryIds.value.size === history.value.length && history.value.length > 0) {
    selectedHistoryIds.value = new Set()
  } else {
    selectedHistoryIds.value = new Set(history.value.map(h => h.id))
  }
}

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

let detailSeq = 0  // 请求序号，防止陈旧响应覆盖新数据

async function viewDetail(logId: number) {
  detailVisible.value = true
  detailLoading.value = true
  currentDetail.value = null
  const seq = ++detailSeq
  try {
    const { data } = await apiClient.get<AnalysisDetail>(`/ai/history/${logId}`)
    if (seq !== detailSeq) return
    currentDetail.value = data
  } catch (e: any) {
    if (seq !== detailSeq) return
    message.error(e.response?.data?.detail || '获取详情失败')
    detailVisible.value = false
  } finally {
    if (seq === detailSeq) detailLoading.value = false
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

// ===== 分析结果对比 =====
interface CompareData {
  inspiration_id: string
  thumbnail_path: string | null
  file_path: string | null
  analyses: Array<{
    id: number; model_name: string; processing_time_ms: number | null
    error: string | null; status: string; created_at: string | null
    parsed_response: Record<string, any> | null
    tags_count: Record<string, number>
  }>
  analyses_count: number
  tag_diff: { added: string[]; removed: string[]; common: string[] } | null
  time_comparison: Array<{ analysis_id: number; model_name: string; processing_time_ms: number | null; created_at: string | null }>
}
const compareVisible = ref(false)
const compareLoading = ref(false)
const compareData = ref<CompareData | null>(null)

let compareSeq = 0  // 请求序号，防止陈旧响应覆盖新数据

async function viewCompare(inspirationId: string) {
  compareVisible.value = true
  compareLoading.value = true
  compareData.value = null
  const seq = ++compareSeq
  try {
    const { data } = await apiClient.get<CompareData>(`/ai/compare/${inspirationId}`)
    if (seq !== compareSeq) return
    compareData.value = data
  } catch (e: any) {
    if (seq !== compareSeq) return
    message.error(e.response?.data?.detail || '获取对比数据失败')
    compareVisible.value = false
  } finally {
    if (seq === compareSeq) compareLoading.value = false
  }
}

const tagCategoryLabel: Record<string, string> = {
  style: '风格', item_type: '单品类型', color: '颜色', fit: '版型',
  body_part: '穿着方式', attribute: '属性',
  outfit: '穿搭大标签',
}

onMounted(() => {
  loadQueue()
  loadHistory()
  loadModelNames()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
  stopBatchPolling()
  if (historyAbort) historyAbort.abort()
})
</script>

<template>
  <div>
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

    <!-- 批量分析任务进度（数据库驱动任务队列） -->
    <n-card v-if="batchTask" size="small" style="margin-bottom:16px">
      <template #header>
        <span>批量分析任务 #{{ batchTask.id }}</span>
        <n-tag
          :type="batchTask.status === 'success' ? 'success' : batchTask.status === 'failed' ? 'error' : batchTask.status === 'cancelled' ? 'default' : 'info'"
          size="small"
          :bordered="false"
          style="margin-left:8px"
        >
          {{ taskStatusLabel[batchTask.status] }}
        </n-tag>
        <n-button
          v-if="['success', 'failed', 'cancelled'].includes(batchTask.status)"
          size="tiny"
          text
          type="default"
          style="margin-left:auto"
          @click="batchTask = null"
        >
          关闭
        </n-button>
      </template>
      <n-progress
        type="line"
        :percentage="batchTask.progress"
        :height="20"
        :status="batchTask.status === 'failed' ? 'error' : batchTask.status === 'success' ? 'success' : undefined"
      />
      <div style="display:flex;align-items:center;gap:12px;margin-top:6px;font-size:12px;color:#888;flex-wrap:wrap">
        <span>{{ batchTask.done }} / {{ batchTask.total }} 已完成</span>
        <span v-if="batchTask.retry_count > 0" style="color:#f0a020">已重试 {{ batchTask.retry_count }} 次</span>
        <span v-if="batchTask.status === 'pending' && batchTask.next_retry_at" style="color:#f0a020">等待自动重试中...</span>
        <n-button
          v-if="batchTask.status === 'pending'"
          size="tiny"
          type="error"
          ghost
          style="margin-left:auto"
          @click="cancelBatchTask"
        >
          取消任务
        </n-button>
      </div>
      <div v-if="batchTask.error" style="font-size:12px;color:#ef4444;margin-top:4px">
        {{ batchTask.error }}
      </div>
    </n-card>

    <!-- 正在分析提示 + 暂停/恢复 -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap">
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
              <n-button size="small" type="error" secondary :loading="clearingFailed">删除所有失败记录</n-button>
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
          { title: '模型', key: 'model_name', width: 130, render: (row: HistoryItem) => h('span', {style:'display:flex;align-items:center;gap:4px'}, [
            row.log_type === 'quality_check' ? h(NTag, {type:'info',size:'tiny',bordered:false}, '审核') : null,
            row.model_name,
          ]) },
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
            ? h('span', { title: row.error, style:'font-size:12px;color:#ef4444;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px;cursor:pointer;text-decoration:underline;text-underline-offset:2px', onClick: () => copyText(row.error!) }, row.error)
            : h('span', {style:'font-size:12px;color:#999'}, '-') },
          { title: '耗时', key: 'time', width: 80, render: (row: HistoryItem) => formatMs(row.processing_time_ms) },
          { title: '时间', key: 'created_at', width: 160, render: (row: HistoryItem) => formatDate(row.created_at) },
          { title: '操作', key: 'actions', width: 140, render: (row: HistoryItem) => h('span', {style:'display:flex;gap:4px'}, [
            h(NButton, {size:'tiny',onClick:()=>viewDetail(row.id)}, row.status === 'success' ? '详情' : '原始输出'),
            h(NButton, {size:'tiny',onClick:()=>viewCompare(row.inspiration_id)}, '对比'),
            row.status === 'error' ? h(NButton, {size:'tiny',onClick:()=>retryAnalysis(row.inspiration_id)}, '重试') : null,
            h(NPopconfirm, {onPositiveClick:()=>deleteLog(row.id)},
              { trigger: ()=>h(NButton,{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此记录？' }
            ),
          ]) },
        ]"
        :data="history" :bordered="false" size="small" :loading="historyLoading"
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

    <!-- 分析详情弹窗 -->
    <n-modal v-model:show="detailVisible" preset="card" title="分析详情" style="max-width:720px" :mask-closable="true">
      <n-spin :show="detailLoading">
        <template v-if="currentDetail">
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

          <div v-if="currentDetail.tags.length > 0">
            <h4 style="margin-bottom:8px">提取的标签</h4>
            <n-space v-for="cat in ['style','item_type','color','fit','body_part','attribute']" :key="cat" style="margin-bottom:8px" align="center">
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

          <n-collapse v-if="currentDetail.raw_response" style="margin-top:16px">
            <n-collapse-item title="AI 原始响应" name="raw">
              <n-code :code="currentDetail.raw_response" language="json" word-wrap />
            </n-collapse-item>
          </n-collapse>
        </template>
      </n-spin>
    </n-modal>

    <!-- 分析结果对比弹窗 -->
    <n-modal v-model:show="compareVisible" preset="card" title="分析结果对比" style="max-width:960px" :mask-closable="true">
      <n-spin :show="compareLoading">
        <template v-if="compareData">
          <div v-if="compareData.thumbnail_path" style="text-align:center;margin-bottom:16px">
            <img :src="getFileUrl(compareData.thumbnail_path)" style="max-height:200px;border-radius:8px" />
          </div>

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
.history-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
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
