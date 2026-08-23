/** AI 分析历史 composable：历史列表、筛选、分页、批量操作与失败处理。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { moveToTrash as moveToTrashApi, type TrashReason } from '@/api/inspirations'
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

  const history = ref<HistoryItem[]>([])
  const historyTotal = ref(0)
  const historyPage = ref(1)
  const historyPageSize = 20
  const historyFilter = ref<string | null>(null)
  const historyModelFilter = ref<string | null>(null)
  const historySearchId = ref('')
  const historyStartDate = ref<number | null>(null)
  const historyEndDate = ref<number | null>(null)
  const historySortBy = ref<string | null>(null)
  const historyLoading = ref(false)
  const selectedHistoryIds = ref<Set<number>>(new Set())
  const historyModelNames = ref<string[]>([])
  const clearingFailed = ref(false)
  const retryingAll = ref(false)

  let historyAbort: AbortController | null = null
  let historySeq = 0 // 请求序号，防止取消竞态导致 loading 提前熄灭

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
      if (historyStartDate.value) params.start_date = toDateStr(historyStartDate.value)
      if (historyEndDate.value) params.end_date = toDateStr(historyEndDate.value)
      if (historySortBy.value) params.sort_by = historySortBy.value
      const { data } = await apiClient.get('/ai/history', { params, signal: historyAbort.signal })
      if (seq !== historySeq) return
      history.value = data.items
      historyTotal.value = data.total
    } catch (e) {
      // 请求被主动取消（切换筛选触发新请求）时不提示错误
      if ((e as { code?: string })?.code !== 'ERR_CANCELED') Message.error('加载历史失败')
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

  /** 时间戳（毫秒）转 YYYY-MM-DD 日期字符串 */
  function toDateStr(ts: number): string {
    const d = new Date(ts)
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  }

  /** 按时间范围筛选 */
  function filterByDate(start: number | null, end: number | null) {
    historyStartDate.value = start
    historyEndDate.value = end
    historyPage.value = 1
    selectedHistoryIds.value = new Set()
    loadHistory()
  }

  /** 按耗时排序（time_asc | time_desc | null=按时间倒序） */
  function sortByTime(value: string | null) {
    historySortBy.value = value
    historyPage.value = 1
    loadHistory()
  }

  /** 导出当前筛选条件下的历史为 CSV 文件 */
  async function exportHistoryCsv() {
    try {
      const params: any = {}
      if (historyFilter.value) params.status = historyFilter.value
      if (historyModelFilter.value) params.model_name = historyModelFilter.value
      if (historySearchId.value.trim()) params.inspiration_id = historySearchId.value.trim()
      if (historyStartDate.value) params.start_date = toDateStr(historyStartDate.value)
      if (historyEndDate.value) params.end_date = toDateStr(historyEndDate.value)
      if (historySortBy.value) params.sort_by = historySortBy.value
      const response = await apiClient.get('/ai/history/export', { params, responseType: 'blob' })
      const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'analysis_history.csv'
      link.click()
      URL.revokeObjectURL(url)
      Message.success('已导出 CSV')
    } catch (e) {
      Message.error(getApiErrorMessage(e, '导出失败'))
    }
  }

  /** 加载历史模型下拉选项 */
  async function loadModelNames() {
    try {
      const { data } = await apiClient.get<{ models: string[] }>('/ai/history/model-names')
      historyModelNames.value = data.models
    } catch {
      /* 静默 */
    }
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
      selectedHistoryIds.value = new Set(history.value.map((h) => h.id))
    }
  }

  /** 批量删除选中的历史记录 */
  async function batchDeleteHistory() {
    if (selectedHistoryIds.value.size === 0) return
    try {
      await apiClient.post('/ai/history/batch-delete', { ids: [...selectedHistoryIds.value] })
      Message.success(`已删除 ${selectedHistoryIds.value.size} 条记录`)
      selectedHistoryIds.value = new Set()
      loadHistory()
      options.loadQueue?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量删除失败'))
    }
  }

  /** 批量重新分析选中的历史记录 */
  async function batchRetryHistory() {
    if (selectedHistoryIds.value.size === 0) return
    try {
      const { data } = await apiClient.post('/ai/history/batch-retry', {
        ids: [...selectedHistoryIds.value],
      })
      Message.success(data.message)
      requestAndNotify('批量重试已启动', { body: data.message, tag: 'batch-retry' })
      selectedHistoryIds.value = new Set()
      loadHistory()
      options.loadActiveAnalyses?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量重试失败'))
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
      Message.success('分析记录已删除')
      loadHistory()
      options.loadQueue?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  /** 将素材移入垃圾桶（软删除，可在垃圾桶恢复）。
   *
   * 用于处置质量审核不到位混入的低质量素材：在分析历史中直接移入垃圾桶，
   * 移入后该素材从正常列表/统计中过滤，历史列表自动刷新。
   *
   * @param inspirationId 素材 ID
   * @param reason 删除原因（质量差/重复/不喜欢/隐私/其他/AI生成），不传时后端自动推断
   */
  async function deleteInspirationFromHistory(inspirationId: string, reason?: TrashReason) {
    try {
      await moveToTrashApi(inspirationId, reason)
      Message.success('素材已移入垃圾桶（可在垃圾桶恢复）')
      loadHistory()
      options.loadQueue?.()
      options.loadActiveAnalyses?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '移入垃圾桶失败'))
    }
  }

  /** 一键重试所有失败记录 */
  async function retryAllFailed() {
    retryingAll.value = true
    try {
      const { data } = await apiClient.post('/ai/retry-all-failed')
      Message.success(data.message || '已加入重试队列')
      requestAndNotify('失败重试已启动', { body: data.message, tag: 'retry-failed' })
      options.loadQueue?.()
      loadHistory()
      options.loadActiveAnalyses?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '重试失败'))
    } finally {
      retryingAll.value = false
    }
  }

  /** 清空所有失败记录 */
  async function deleteAllFailed() {
    clearingFailed.value = true
    try {
      const { data } = await apiClient.delete('/ai/history/failed/all')
      Message.success(data.message || '已清空失败记录')
      loadHistory()
      options.loadQueue?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '清空失败'))
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
    historyStartDate,
    historyEndDate,
    historySortBy,
    historyLoading,
    selectedHistoryIds,
    historyModelNames,
    clearingFailed,
    retryingAll,
    loadHistory,
    filterHistory,
    filterByModel,
    searchById,
    filterByDate,
    sortByTime,
    exportHistoryCsv,
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
