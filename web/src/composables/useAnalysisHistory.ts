/** AI 分析历史 composable：历史列表、筛选、分页、批量操作与失败处理。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { moveToTrash as moveToTrashApi } from '@/api/inspirations'
import { useNotification } from '@/composables/useNotification'
import type { HistoryItem } from '@/types/analysis'

/** 分析历史 composable 配置 */
export interface UseAnalysisHistoryOptions {
  /** 队列统计刷新回调 */
  loadQueue?: () => void
  /** 活动分析刷新回调 */
  loadActiveAnalyses?: () => void
}

export function useAnalysisHistory(options: UseAnalysisHistoryOptions = {}) {
  const { requestAndNotify } = useNotification()
  const message = useMessage()

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
  const clearingFailed = ref(false)
  const retryingAll = ref(false)

  let historyAbort: AbortController | null = null
  let historySeq = 0  // 请求序号，防止取消竞态导致 loading 提前熄灭

  /** 加载分析历史列表 */
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

  /** 按状态筛选 */
  function filterHistory(status: string | null) {
    historyFilter.value = status
    historyPage.value = 1
    selectedHistoryIds.value = new Set()
    loadHistory()
  }

  /** 按模型筛选 */
  function filterByModel(model: string | null) {
    historyModelFilter.value = model
    historyPage.value = 1
    selectedHistoryIds.value = new Set()
    loadHistory()
  }

  /** 按素材 ID 搜索 */
  function searchById() {
    historyPage.value = 1
    selectedHistoryIds.value = new Set()
    loadHistory()
  }

  /** 加载历史模型下拉选项 */
  async function loadModelNames() {
    try {
      const { data } = await apiClient.get<{ models: string[] }>('/ai/history/model-names')
      historyModelNames.value = data.models
    } catch { /* 静默 */ }
  }

  /** 切换单条记录选中状态 */
  function toggleSelectHistory(logId: number) {
    const next = new Set(selectedHistoryIds.value)
    if (next.has(logId)) next.delete(logId)
    else next.add(logId)
    selectedHistoryIds.value = next
  }

  /** 全选 / 取消全选 */
  function selectAllHistory() {
    if (selectedHistoryIds.value.size === history.value.length && history.value.length > 0) {
      selectedHistoryIds.value = new Set()
    } else {
      selectedHistoryIds.value = new Set(history.value.map(h => h.id))
    }
  }

  /** 批量删除选中的历史记录 */
  async function batchDeleteHistory() {
    if (selectedHistoryIds.value.size === 0) return
    try {
      await apiClient.post('/ai/history/batch-delete', { ids: [...selectedHistoryIds.value] })
      message.success(`已删除 ${selectedHistoryIds.value.size} 条记录`)
      selectedHistoryIds.value = new Set()
      loadHistory()
      options.loadQueue?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '批量删除失败')
    }
  }

  /** 批量重新分析选中的历史记录 */
  async function batchRetryHistory() {
    if (selectedHistoryIds.value.size === 0) return
    try {
      const { data } = await apiClient.post('/ai/history/batch-retry', { ids: [...selectedHistoryIds.value] })
      message.success(data.message)
      requestAndNotify('批量重试已启动', { body: data.message, tag: 'batch-retry' })
      selectedHistoryIds.value = new Set()
      loadHistory()
      options.loadActiveAnalyses?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '批量重试失败')
    }
  }

  /** 分页切换 */
  function onHistoryPageChange(page: number) {
    historyPage.value = page
    loadHistory()
  }

  /** 删除单条历史记录 */
  async function deleteLog(logId: number) {
    try {
      await apiClient.delete(`/ai/history/${logId}`)
      message.success('分析记录已删除')
      loadHistory()
      options.loadQueue?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败')
    }
  }

  /** 将素材移入垃圾桶（软删除，30 天内可恢复）。
   *
   * 用于处置质量审核不到位混入的低质量素材：在分析历史中直接移入垃圾桶，
   * 移入后该素材从正常列表/统计中过滤，历史列表自动刷新。
   */
  async function deleteInspirationFromHistory(inspirationId: string) {
    try {
      await moveToTrashApi(inspirationId)
      message.success('素材已移入垃圾桶（30 天内可恢复）')
      loadHistory()
      options.loadQueue?.()
      options.loadActiveAnalyses?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '移入垃圾桶失败')
    }
  }

  /** 一键重试所有失败记录 */
  async function retryAllFailed() {
    retryingAll.value = true
    try {
      const { data } = await apiClient.post('/ai/retry-all-failed')
      message.success(data.message || '已加入重试队列')
      requestAndNotify('失败重试已启动', { body: data.message, tag: 'retry-failed' })
      options.loadQueue?.(); loadHistory(); options.loadActiveAnalyses?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '重试失败')
    } finally {
      retryingAll.value = false
    }
  }

  /** 清空所有失败记录 */
  async function deleteAllFailed() {
    clearingFailed.value = true
    try {
      const { data } = await apiClient.delete('/ai/history/failed/all')
      message.success(data.message || '已清空失败记录')
      loadHistory()
      options.loadQueue?.()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '清空失败')
    } finally {
      clearingFailed.value = false
    }
  }

  /** 中止在途的历史请求（组件卸载时调用） */
  function abortHistoryRequest() {
    if (historyAbort) historyAbort.abort()
  }

  return {
    history,
    historyTotal,
    historyPage,
    historyPageSize,
    historyFilter,
    historyModelFilter,
    historySearchId,
    historyLoading,
    selectedHistoryIds,
    historyModelNames,
    clearingFailed,
    retryingAll,
    loadHistory,
    filterHistory,
    filterByModel,
    searchById,
    loadModelNames,
    toggleSelectHistory,
    selectAllHistory,
    batchDeleteHistory,
    batchRetryHistory,
    onHistoryPageChange,
    deleteLog,
    deleteInspirationFromHistory,
    retryAllFailed,
    deleteAllFailed,
    abortHistoryRequest,
  }
}
