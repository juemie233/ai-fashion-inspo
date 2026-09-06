/** AI 分析队列 composable：队列统计、活动分析、批量任务、排队素材与轮询。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { warnShape } from '@/utils/apiGuard'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { useNotification } from '@/composables/useNotification'
import { subscribeWs, isWsConnected } from '@/composables/useWebSocket'
import { isTaskTerminalStatus } from '@/types/task'
import type {
  QueueStats,
  ActiveAnalysis,
  TaskInfo,
  QueueItem,
  MultiAnalyzeParams,
} from '@/types/analysis'

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
/** WS 已连接时的保底轮询间隔（毫秒）：单条分析完成由 ai_analysis_done 推送驱动 */
const WS_CONNECTED_POLL_MS = 15000

export function useAnalysisQueue(options: UseAnalysisQueueOptions = {}) {
  const { requestAndNotify, checkFailureAlert } = useNotification()

  const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
  const activeAnalyses = ref<Record<string, string>>({})
  const batchAnalyzing = ref(false)
  const batchTask = ref<TaskInfo | null>(null)
  /** 分析任务列表（batch/multi，仅 pending/running/paused 等未完成任务——
   *  完成的批量分析不再展示，避免挤占队列区；终态在「任务管理」页可查可删），按 id 倒序 */
  const analysisTasks = ref<TaskInfo[]>([])
  const pendingQueue = ref<QueueItem[]>([])
  const queuePaused = ref(false)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollSeq = 0 // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排
  let batchSettled = false // 批量任务终态幂等标记：WS 推送与轮询合流后副作用只执行一次

  /** 加载排队中素材 */
  async function loadPendingQueue() {
    try {
      const { data } = await apiClient.get<{ items: QueueItem[]; paused: boolean }>(
        '/ai/queue/pending',
      )
      pendingQueue.value = data.items
      queuePaused.value = data.paused
    } catch {}
  }

  /** 加载分析任务列表（batch_analyze + multi_analyze）。
   *  后端 /tasks 不传 status 即返回全部状态、按 id 倒序，分类型各取近 50 合并。
   *  只保留未完成任务（pending/running/paused）：完成（success/failed/cancelled）
   *  的批量分析不再展示，其进度与结果可从任务管理页 / 分析历史查看。 */
  async function loadAnalysisTasks() {
    try {
      const [legacy, multi] = await Promise.all([
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'batch_analyze', size: 50 },
        }),
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'multi_analyze', size: 50 },
        }),
      ])
      const merged = [...legacy.data.items, ...multi.data.items].sort((a, b) => b.id - a.id)
      // 只保留未完成任务（含 paused）：暂停任务不能被数量上限挤掉，需始终可见可恢复
      analysisTasks.value = merged.filter((t) => !isTaskTerminalStatus(t.status))
    } catch {
      /* 静默：列表为空不影响其它功能 */
    }
  }

  /** 终态任务即时从列表移除（完成即不再展示；配合轮询/WS 就地更新使用） */
  function dropTerminalTasks() {
    if (analysisTasks.value.some((t) => isTaskTerminalStatus(t.status))) {
      analysisTasks.value = analysisTasks.value.filter((t) => !isTaskTerminalStatus(t.status))
    }
  }

  /** 行级暂停任务（运行中批量/组合分析） */
  async function pauseTaskById(taskId: number) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${taskId}/pause`)
      Message.success(data?.message || '任务已暂停')
      const row = analysisTasks.value.find((t) => t.id === taskId)
      if (row) row.status = 'paused'
      if (batchTask.value?.id === taskId) batchTask.value = { ...batchTask.value, status: 'paused' }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '暂停失败'))
    }
  }

  /** 行级恢复任务（已暂停的批量/组合分析） */
  async function resumeTaskById(taskId: number) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${taskId}/resume`)
      Message.success(data?.message || '任务已恢复')
      // 恢复后接管对该任务的轮询（若当前没有跟踪更新的活动任务）
      await loadAnalysisTasks()
      const resumed = analysisTasks.value.find((t) => t.id === taskId)
      if (resumed && !isTaskTerminalStatus(resumed.status)) {
        const currentActive = batchTask.value && !isTaskTerminalStatus(batchTask.value.status)
        if (!currentActive || (batchTask.value?.id ?? -Infinity) < taskId) {
          batchTask.value = resumed
          startBatchPolling(taskId)
        }
      }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '恢复失败'))
    }
  }

  /** 行级取消任务（pending 取消=删除记录；running 分析类不支持硬取消则后端提示） */
  async function cancelTaskById(taskId: number) {
    try {
      const { data } = await apiClient.post<{ message?: string; deleted?: boolean }>(
        `/tasks/${taskId}/cancel`,
      )
      Message.success(data?.message || '任务已取消')
      if (data.deleted) {
        analysisTasks.value = analysisTasks.value.filter((t) => t.id !== taskId)
      } else {
        const row = analysisTasks.value.find((t) => t.id === taskId)
        if (row) {
          row.status = 'cancelled'
          dropTerminalTasks() // cancelled 为终态：完成即不再展示
        }
      }
      loadQueue()
      loadActiveAnalyses()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  /** 把「最新非终态任务」接入现有单任务轮询/通知机制（供操作与刷新后调用） */
  function syncActiveTask() {
    const active = analysisTasks.value.find((t) => !isTaskTerminalStatus(t.status))
    if (active && active.id !== batchTask.value?.id) {
      batchTask.value = active
      startBatchPolling(active.id)
    }
  }

  /** 取消排队中的单个素材 */
  async function cancelQueueItem(inspirationId: string) {
    try {
      await apiClient.delete(`/ai/queue/${inspirationId}`)
      Message.success('已取消')
      loadPendingQueue()
      loadActiveAnalyses()
    } catch (e) {
      const data = (e as { response?: { data?: { detail?: string; message?: string } } })?.response
        ?.data
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
    } catch {
      Message.error('操作失败')
    }
  }

  /** 加载队列统计 */
  async function loadQueue() {
    try {
      const { data } = await apiClient.get<QueueStats>('/ai/queue')
      // 校验统计字段（此前后端口径问题曾致 unanalyzed 恒为 0、按钮误禁用）
      queueStats.value = warnShape(
        data,
        {
          total: 'number',
          analyzed: 'number',
          unanalyzed: 'number',
          failed: 'number',
        },
        '/ai/queue',
      )
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

  /** 开始轮询活动分析（按是否有活动任务自动调整间隔；WS 已连接时推送驱动为主） */
  function startPolling() {
    loadActiveAnalyses()
    scheduleNextPoll()
  }

  function scheduleNextPoll() {
    const wasActive = Object.keys(activeAnalyses.value).length > 0
    const interval = isWsConnected()
      ? WS_CONNECTED_POLL_MS
      : wasActive
        ? ACTIVE_POLL_MS
        : IDLE_POLL_MS
    pollTimer = setTimeout(async () => {
      await loadActiveAnalyses()
      loadPendingQueue()
      loadAnalysisTasks()
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
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  /** 创建批量分析任务并开始轮询（支持可选的多模型 × 多提示词组合参数） */
  async function triggerBatchAnalyze(multi?: MultiAnalyzeParams) {
    batchAnalyzing.value = true
    try {
      const { data } = await apiClient.get<{ ids: string[]; count: number }>('/ai/unanalyzed-ids')
      if (data.count === 0) {
        Message.info('所有素材均已分析过，无需重复分析')
        return
      }
      // 请求体：传了组合参数用对象格式（多模型 × 多提示词），否则保持旧版数组格式
      const isMulti = !!multi
      const body = isMulti
        ? {
            inspiration_ids: data.ids,
            models: multi!.models,
            prompt_ids: multi!.promptIds,
            apply_tags: multi!.applyTags,
          }
        : data.ids
      // 创建批量分析任务，立即拿到 task_id，后续轮询任务状态
      const { data: created } = await apiClient.post<{
        task_id: number
        message: string
        count: number
        skipped: number
        combinations?: number
        status?: string
        ollama_will_start?: boolean
      }>('/ai/batch-analyze', body)
      if (created.ollama_will_start) {
        Message.warning(created.message || 'Ollama 正在启动中，请等待后刷新重试')
        batchAnalyzing.value = false
        return
      }
      batchTask.value = {
        id: created.task_id,
        type: isMulti ? 'multi_analyze' : 'batch_analyze',
        status: 'pending',
        progress: 0,
        total: isMulti ? (created.count ?? 0) * (created.combinations ?? 1) : created.count,
        done: 0,
        result: null,
        error: null,
        retry_count: 0,
        max_retries: 2,
        next_retry_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      const comboSuffix = isMulti ? `，${created.combinations} 个组合` : ''
      Message.success(
        `已创建${isMulti ? '组合' : '批量'}分析任务 #${created.task_id}，共 ${created.count} 个素材${comboSuffix}`,
      )
      requestAndNotify(isMulti ? '组合分析已创建' : '批量分析已创建', {
        body: `任务 #${created.task_id}，${created.count} 个素材${comboSuffix}已加入队列`,
        tag: isMulti ? 'multi-analyze' : 'batch-analyze',
      })
      // 立即并入任务列表（随轮询刷新校正真实状态）
      analysisTasks.value = [batchTask.value, ...analysisTasks.value]
      startBatchPolling(created.task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量分析失败'))
    } finally {
      batchAnalyzing.value = false
    }
  }

  /** 轮询批量分析任务状态（约 1 秒一次；WS 已连接时 15 秒兜底），完成后刷新分析结果 */
  function startBatchPolling(taskId: number) {
    stopBatchPolling()
    batchSettled = false
    const seq = batchPollSeq // 当前代际：stopBatchPolling 已自增，旧链的 seq 与之不符即失效
    let consecutiveFailures = 0 // 连续失败次数，失败时有限次重试而非直接停止
    const poll = async () => {
      if (seq !== batchPollSeq) return // 已被 stop/新轮询取代，不再调度
      try {
        const { data } = await apiClient.get<TaskInfo>(`/tasks/${taskId}`)
        if (seq !== batchPollSeq) return // 在途请求返回前已被停止，丢弃结果
        consecutiveFailures = 0
        handleBatchSnapshot(data)
        if (batchTask.value && !isTaskTerminalStatus(batchTask.value.status)) {
          const interval = isWsConnected() ? WS_CONNECTED_POLL_MS : BATCH_POLL_MS
          batchPollTimer = setTimeout(poll, interval)
        }
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

  /**
   * 统一处理一次批量任务快照（轮询返回 / WS 推送合流后共用）：
   * 更新 batchTask 并处理终态副作用（提示/刷新）。batchSettled 幂等标记保证
   * 终态副作用只执行一次——WS 推送与轮询谁先到谁触发。
   */
  function handleBatchSnapshot(data: TaskInfo) {
    batchTask.value = data
    // 同步到任务列表行（列表与单任务轮询共享同一任务状态）
    const row = analysisTasks.value.find((t) => t.id === data.id)
    if (row) Object.assign(row, data)
    // 完成即从「分析任务」列表移除（不再展示已完成的批量分析）
    if (isTaskTerminalStatus(data.status)) dropTerminalTasks()
    if (!isTaskTerminalStatus(data.status)) return
    if (batchSettled) return
    batchSettled = true
    stopBatchPolling()
    const label = data.type === 'multi_analyze' ? '组合分析' : '批量分析'
    if (data.status === 'success') {
      const successCount = data.result?.success_count
      const failedCount = data.result?.failed_count
      const detail =
        successCount !== undefined && failedCount !== undefined
          ? `成功 ${successCount}，失败 ${failedCount}`
          : '已完成'
      Message.success(`${label}完成：${detail}`)
    } else if (data.status === 'failed') {
      Message.error(`${label}失败：${data.error || '未知错误'}`)
    } else {
      Message.info(`${label}任务已取消`)
    }
    loadQueue()
    options.loadHistory?.()
    loadActiveAnalyses()
    loadAnalysisTasks()
  }

  /** 停止批量任务轮询（自增代际号，使当前轮询链失效） */
  function stopBatchPolling() {
    batchPollSeq += 1 // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
    if (batchPollTimer) {
      clearTimeout(batchPollTimer)
      batchPollTimer = null
    }
  }

  /** 恢复进行中的批量/组合分析任务：刷新页面后拉取全状态任务列表，
   *  并对最新非终态任务（含暂停的）接管轮询，保证暂停任务可见可恢复 */
  async function resumeBatchAnalyzeTask() {
    await loadAnalysisTasks()
    syncActiveTask()
  }

  // ── WebSocket 推送合流 ──
  // 1) task_event：与批量轮询同源的任务事件，即时合并 batchTask 进度/状态
  //    （settled 标记保证终态副作用只执行一次，与 useAdminTask 同一套模式）
  subscribeWs('task_event', (raw) => {
    const ev = raw as {
      task_id?: number
      status?: string
      progress?: number
      done?: number
      total?: number
      error?: string | null
    }
    if (!ev || !ev.task_id) return
    const current = batchTask.value
    if (current && ev.task_id === current.id && !batchSettled) {
      handleBatchSnapshot({
        ...current,
        status: ev.status ?? current.status,
        progress: typeof ev.progress === 'number' ? ev.progress : current.progress,
        done: typeof ev.done === 'number' ? ev.done : current.done,
        total: typeof ev.total === 'number' ? ev.total : current.total,
        error: ev.error !== undefined ? ev.error : current.error,
      })
      return
    }
    // 事件属于任务列表中的其它任务：就地更新该行（列表刷新也兜底）
    const row = analysisTasks.value.find((t) => t.id === ev.task_id)
    if (row) {
      if (ev.status) row.status = ev.status
      if (typeof ev.progress === 'number') row.progress = ev.progress
      if (typeof ev.done === 'number') row.done = ev.done
      if (typeof ev.total === 'number') row.total = ev.total
      if (ev.error !== undefined) row.error = ev.error
      // 完成即从「分析任务」列表移除（不再展示已完成的批量分析）
      if (isTaskTerminalStatus(row.status)) dropTerminalTasks()
    }
  })
  // 2) ai_analysis_done：单素材分析完成（API 进程内广播）→ 即时刷新队列与历史
  subscribeWs('ai_analysis_done', () => {
    loadActiveAnalyses()
    loadQueue()
    options.loadHistory?.()
  })

  /** 单条失败记录重新加入分析队列 */
  async function retryAnalysis(id: string) {
    try {
      const { data } = await apiClient.post<Record<string, unknown>>(`/ai/retry/${id}`)
      if ((data as Record<string, unknown>).ollama_will_start === true) {
        Message.warning(String((data as Record<string, unknown>).message || 'Ollama 正在启动中'))
      } else {
        Message.success('已重新加入队列')
      }
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
    analysisTasks,
    pendingQueue,
    queuePaused,
    loadQueue,
    loadActiveAnalyses,
    loadPendingQueue,
    loadAnalysisTasks,
    cancelQueueItem,
    togglePauseQueue,
    triggerBatchAnalyze,
    pauseTaskById,
    resumeTaskById,
    cancelTaskById,
    retryAnalysis,
    startPolling,
    stopPolling,
    stopBatchPolling,
    resumeBatchAnalyzeTask,
  }
}
