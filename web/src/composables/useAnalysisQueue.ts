/** AI 分析队列 composable：队列统计、活动分析、批量任务、排队素材与轮询。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { useNotification } from '@/composables/useNotification'
import type { QueueStats, ActiveAnalysis, TaskInfo, QueueItem } from '@/types/analysis'

/** 分析队列 composable 配置 */
export interface UseAnalysisQueueOptions {
  /** 分析历史刷新回调：轮询 / 批量任务完成时触发 */
  loadHistory?: () => void
}

/** 有活动分析时的轮询间隔（毫秒） */
const ACTIVE_POLL_MS = 3000
/** 无活动分析时的轮询间隔（毫秒） */
const IDLE_POLL_MS = 15000
/** 批量任务轮询正常间隔（毫秒） */
const BATCH_POLL_MS = 1000
/** 批量任务轮询失败重试间隔（毫秒） */
const BATCH_RETRY_MS = 3000

export function useAnalysisQueue(options: UseAnalysisQueueOptions = {}) {
  const { requestAndNotify, checkFailureAlert } = useNotification()
  
  const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
  const activeAnalyses = ref<Record<string, string>>({})
  const batchAnalyzing = ref(false)
  const batchTask = ref<TaskInfo | null>(null)
  const pendingQueue = ref<QueueItem[]>([])
  const queuePaused = ref(false)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollSeq = 0  // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排

  /** 加载排队中素材 */
  async function loadPendingQueue() {
    try {
      const { data } = await apiClient.get<{ items: QueueItem[]; paused: boolean }>('/ai/queue/pending')
      pendingQueue.value = data.items
      queuePaused.value = data.paused
    } catch {}
  }

  /** 取消排队中的单个素材 */
  async function cancelQueueItem(inspirationId: string) {
    try {
      await apiClient.delete(`/ai/queue/${inspirationId}`)
      Message.success('已取消')
      loadPendingQueue()
      loadActiveAnalyses()
    } catch (e) {
      const data = (e as { response?: { data?: { detail?: string; message?: string } } })
        ?.response?.data
      Message.error(data?.detail || data?.message || '取消失败')
    }
  }

  /** 暂停 / 恢复分析队列 */
  async function togglePauseQueue() {
    try {
      if (queuePaused.value) {
        await apiClient.post('/ai/queue/resume')
        Message.success('队列已恢复')
      } else {
        await apiClient.post('/ai/queue/pause')
        Message.success('队列已暂停')
      }
      loadPendingQueue()
    } catch (e) {
      Message.error('操作失败')
    }
  }

  /** 加载队列统计 */
  async function loadQueue() {
    try {
      const { data } = await apiClient.get<QueueStats>('/ai/queue')
      queueStats.value = data
      checkFailureAlert(data.failed, data.total)
    } catch {}
  }

  /** 加载正在进行的分析任务 */
  async function loadActiveAnalyses() {
    try {
      const { data } = await apiClient.get<ActiveAnalysis>('/ai/active-analyses')
      activeAnalyses.value = data.active_analyses || {}
    } catch {}
  }

  /** 开始轮询活动分析（按是否有活动任务自动调整间隔） */
  function startPolling() {
    loadActiveAnalyses()
    scheduleNextPoll()
  }

  function scheduleNextPoll() {
    const wasActive = Object.keys(activeAnalyses.value).length > 0
    const interval = wasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS
    pollTimer = setTimeout(async () => {
      await loadActiveAnalyses()
      loadPendingQueue()
      const isActive = Object.keys(activeAnalyses.value).length > 0
      loadQueue()
      if (isActive || wasActive) {
        options.loadHistory?.()
      }
      if (pollTimer !== null) scheduleNextPoll()
    }, interval)
  }

  /** 停止活动分析轮询 */
  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  }

  /** 创建批量分析任务并开始轮询 */
  async function triggerBatchAnalyze() {
    batchAnalyzing.value = true
    try {
      const { data } = await apiClient.get<{ ids: string[]; count: number }>('/ai/unanalyzed-ids')
      if (data.count === 0) {
        Message.info('所有素材均已分析过，无需重复分析')
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
      Message.success(`已创建批量分析任务 #${created.task_id}，共 ${created.count} 个素材`)
      requestAndNotify('批量分析已创建', { body: `任务 #${created.task_id}，${created.count} 个素材已加入队列`, tag: 'batch-analyze' })
      startBatchPolling(created.task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量分析失败'))
    } finally {
      batchAnalyzing.value = false
    }
  }

  /** 轮询批量分析任务状态（约 1 秒一次），完成后刷新分析结果 */
  function startBatchPolling(taskId: number) {
    stopBatchPolling()
    const seq = batchPollSeq  // 当前代际：stopBatchPolling 已自增，旧链的 seq 与之不符即失效
    let consecutiveFailures = 0  // 连续失败次数，失败时有限次重试而非直接停止
    const poll = async () => {
      if (seq !== batchPollSeq) return  // 已被 stop/新轮询取代，不再调度
      try {
        const { data } = await apiClient.get<TaskInfo>(`/tasks/${taskId}`)
        if (seq !== batchPollSeq) return  // 在途请求返回前已被停止，丢弃结果
        consecutiveFailures = 0
        batchTask.value = data
        if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
          stopBatchPolling()
          if (data.status === 'success') {
            const successCount = data.result?.success_count
            const failedCount = data.result?.failed_count
            const detail = (successCount !== undefined && failedCount !== undefined)
              ? `成功 ${successCount}，失败 ${failedCount}`
              : '已完成'
            Message.success(`批量分析完成：${detail}`)
          } else if (data.status === 'failed') {
            Message.error(`批量分析失败：${data.error || '未知错误'}`)
          } else {
            Message.info('批量分析任务已取消')
          }
          loadQueue(); options.loadHistory?.(); loadActiveAnalyses()
          return
        }
        batchPollTimer = setTimeout(poll, BATCH_POLL_MS)
      } catch {
        if (seq !== batchPollSeq) return
        consecutiveFailures += 1
        if (consecutiveFailures >= 5) {
          // 连续多次失败才停止，避免后端重启/网络抖动导致任务进度卡死
          stopBatchPolling()
          Message.error('获取任务状态多次失败，已停止轮询，请稍后手动刷新')
          return
        }
        // 有限次重试：间隔放大到 3 秒，继续续排轮询链
        batchPollTimer = setTimeout(poll, BATCH_RETRY_MS)
      }
    }
    poll()
  }

  /** 取消排队中的批量分析任务 */
  async function cancelBatchTask() {
    if (!batchTask.value) return
    try {
      await apiClient.post(`/tasks/${batchTask.value.id}/cancel`)
      Message.success('任务已取消')
      stopBatchPolling()
      batchTask.value = { ...batchTask.value, status: 'cancelled' }
      loadQueue()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  /** 停止批量任务轮询（自增代际号，使当前轮询链失效） */
  function stopBatchPolling() {
    batchPollSeq += 1  // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
    if (batchPollTimer) { clearTimeout(batchPollTimer); batchPollTimer = null }
  }

  /** 恢复进行中的批量分析任务：刷新页面后查询是否有 pending/running 的批量分析任务并继续轮询 */
  async function resumeBatchAnalyzeTask() {
    try {
      const { data } = await apiClient.get<{ items: TaskInfo[] }>('/tasks', {
        params: { type: 'batch_analyze', size: 20 },
      })
      const active = data.items.find((t) => t.status === 'pending' || t.status === 'running')
      if (active) {
        batchTask.value = active
        startBatchPolling(active.id)
      }
    } catch { /* 静默 */ }
  }

  /** 单条失败记录重新加入分析队列 */
  async function retryAnalysis(id: string) {
    try {
      await apiClient.post(`/ai/retry/${id}`)
      Message.success('已重新加入队列')
      loadQueue()
      loadActiveAnalyses()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '重试失败'))
    }
  }

  return {
    queueStats,
    activeAnalyses,
    batchAnalyzing,
    batchTask,
    pendingQueue,
    queuePaused,
    loadQueue,
    loadActiveAnalyses,
    loadPendingQueue,
    cancelQueueItem,
    togglePauseQueue,
    triggerBatchAnalyze,
    cancelBatchTask,
    retryAnalysis,
    startPolling,
    stopPolling,
    stopBatchPolling,
    resumeBatchAnalyzeTask,
  }
}
