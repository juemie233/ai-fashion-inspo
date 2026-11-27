/** AI 分析队列 composable：队列统计、分析任务列表（batch/multi，含单图分析）与轮询。
 *
 * 2026-11-16（R2）：单图分析并入 worker 任务队列（POST /ai/analyze 只入队返回 task_id），
 * API 进程内的内存分析队列已删除。因此本 composable 不再访问
 * /ai/queue/pending、/ai/queue/pause|resume、DELETE /ai/queue/{id}、/ai/active-analyses，
 * 数据源收敛为「/tasks?type=batch_analyze|multi_analyze + WS task_event + /ai/queue 统计」。
 * 单图分析即以 ``total=1`` 的 batch_analyze 任务出现在任务列表里。
 */

import { getApiErrorMessage } from '@/utils/apiError'
import { warnShape } from '@/utils/apiGuard'
import { getCurrentScope, onScopeDispose, ref } from 'vue'
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

/** 有未完成任务时的轮询间隔（毫秒） */
const ACTIVE_POLL_MS = 3000
/** 无未完成任务时的轮询间隔（毫秒） */
const IDLE_POLL_MS = 15000
/** 批量任务轮询正常间隔（毫秒） */
const BATCH_POLL_MS = 1000
/** 批量任务轮询失败重试间隔（毫秒） */
const BATCH_RETRY_MS = 3000
/** WS 已连接时的保底轮询间隔（毫秒）：任务进度/终态由 task_event 推送驱动 */
const WS_CONNECTED_POLL_MS = 15000

export function useAnalysisQueue(options: UseAnalysisQueueOptions = {}) {
  const { requestAndNotify, checkFailureAlert } = useNotification()

  const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })
  /** 「正在分析」映射：旧 /ai/active-analyses 已删除（R2），该数据源不存在。
   *  保留空对象仅为兼容 AnalysisPanel/AnalysisQueueOverview 既有 props 契约（其区块按空值隐藏）；
   *  单图分析现作为 batch_analyze(total=1) 任务在 analysisTasks 里展示。
   *  类型取自 ActiveAnalysis，避免该类型定义变成死导出。 */
  const activeAnalyses = ref<ActiveAnalysis['active_analyses']>({})
  const batchAnalyzing = ref(false)
  const batchTask = ref<TaskInfo | null>(null)
  /** 分析任务列表（batch/multi，仅 pending/running/paused 等未完成任务——
   *  完成的批量分析不再展示，避免挤占队列区；终态在「任务管理」页可查可删），按 id 倒序 */
  const analysisTasks = ref<TaskInfo[]>([])
  /** 排队中素材：旧 GET /ai/queue/pending 已删除（R2）；保留空数组仅为兼容既有 UI props 契约 */
  const pendingQueue = ref<QueueItem[]>([])
  /** 队列整体暂停：旧 /ai/queue/pause|resume 已删除（R2）；恒为 false，对应按钮随之隐藏。
   *  需要暂停某次分析请用任务行级暂停（pauseTaskById）。 */
  const queuePaused = ref(false)

  let tasksPollSeq = 0 // 任务列表轮询代际号
  let tasksLoadSeq = 0 // 任务列表加载序号：并发请求只允许最新一次写回
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollSeq = 0 // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排
  let batchSettled = false // 批量任务终态幂等标记：WS 推送与轮询合流后副作用只执行一次

  /** 兼容占位：旧 GET /ai/queue/pending 已随 API 内存分析队列删除（2026-11-16 R2）。
   *  单图/批量分析现在都以任务形式存在，统一由 loadAnalysisTasks 展示与轮询；
   *  保留函数签名是为了不改动既有调用方（含 useAnalysisHistory 的回调注入）。 */
  async function loadPendingQueue() {
    pendingQueue.value = []
  }

  /** 加载分析任务列表（batch_analyze + multi_analyze）。
   *  后端 /tasks 不传 status 即返回全部状态、按 id 倒序，分类型各取近 50 合并。
   *  只保留未完成任务（pending/running/paused）：完成（success/failed/cancelled）
   *  的批量分析不再展示，其进度与结果可从任务管理页 / 分析历史查看。 */
  async function loadAnalysisTasks() {
    const seq = ++tasksLoadSeq
    try {
      // 除按类型取最近任务外，再单独查 paused：只取全状态前 50 再前端过滤时，
      // 近期终态任务会把更早的暂停任务挤出 50 条，导致它不可见、无法恢复。
      const [legacy, multi, legacyPaused, multiPaused] = await Promise.all([
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'batch_analyze', size: 50 },
        }),
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'multi_analyze', size: 50 },
        }),
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'batch_analyze', status: 'paused', size: 50 },
        }),
        apiClient.get<{ items: TaskInfo[] }>('/tasks', {
          params: { type: 'multi_analyze', status: 'paused', size: 50 },
        }),
      ])
      if (seq !== tasksLoadSeq) return // 慢的旧响应不能覆盖新响应
      const byId = new Map<number, TaskInfo>()
      for (const item of [
        ...legacy.data.items,
        ...multi.data.items,
        ...legacyPaused.data.items,
        ...multiPaused.data.items,
      ]) {
        byId.set(item.id, item)
      }
      const merged = [...byId.values()].sort((a, b) => b.id - a.id)
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

  /** 行级取消任务（pending 取消=删除记录；running 的批量/组合分析等支持运行中取消，
   *  标记 cancelled 停止执行，已产出的分析日志与标签全部保留） */
  async function cancelTaskById(taskId: number) {
    try {
      const { data } = await apiClient.post<{ message?: string; deleted?: boolean }>(
        `/tasks/${taskId}/cancel`,
      )
      Message.success(data?.message || '任务已取消')
      if (data?.deleted) {
        analysisTasks.value = analysisTasks.value.filter((t) => t.id !== taskId)
        // pending 取消等于物理删除：被跟踪任务必须一起停掉，否则后续 GET 404
        // 会被误算成「获取任务状态失败」，5 次后弹错误让用户以为出了故障
        if (batchTask.value?.id === taskId) {
          stopBatchPolling()
          batchTask.value = null
        }
      } else {
        const row = analysisTasks.value.find((t) => t.id === taskId)
        if (row) {
          row.status = 'cancelled'
          dropTerminalTasks() // cancelled 为终态：完成即不再展示
        }
      }
      loadQueue()
    } catch (e) {
      const status = (e as { response?: { status?: number } } | undefined)?.response?.status
      if (status === 404) {
        // 任务已被其它入口删除：静默移除，不当成失败
        analysisTasks.value = analysisTasks.value.filter((t) => t.id !== taskId)
        if (batchTask.value?.id === taskId) {
          stopBatchPolling()
          batchTask.value = null
        }
        return
      }
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  /** 把「最新非终态任务」接入现有单任务轮询/通知机制（供操作与刷新后调用） */
  function syncActiveTask() {
    const active = analysisTasks.value.find((t) => !isTaskTerminalStatus(t.status))
    if (!active) return
    // 同一任务但轮询链已因连续失败停掉时，也要重启，否则用户点刷新也无法恢复跟踪
    if (active.id !== batchTask.value?.id || batchPollTimer === null) {
      batchTask.value = active
      startBatchPolling(active.id)
    }
  }

  /** 兼容占位：旧 DELETE /ai/queue/{id} 已删除（2026-11-16 R2，按素材取消不再支持）。
   *  单图分析现为 batch_analyze(total=1) 任务，请走任务列表的行级取消（cancelTaskById）。 */
  async function cancelQueueItem(_inspirationId: string) {
    Message.info('单图分析已并入任务队列，请在下方任务列表中取消')
  }

  /** 兼容占位：旧 /ai/queue/pause|resume 已删除（2026-11-16 R2，worker 队列不支持整队暂停）。
   *  需要暂停分析请在任务列表按任务暂停（pauseTaskById）。 */
  async function togglePauseQueue() {
    Message.info('队列整体暂停已下线，请在任务列表中按任务暂停')
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
      checkFailureAlert(queueStats.value.failed, queueStats.value.total)
    } catch {
      /* 静默：统计失败不影响任务列表与轮询 */
    }
  }

  /** 兼容占位：旧 GET /ai/active-analyses 已删除（2026-11-16 R2，API 进程不再内存分析）。
   *  正在进行的分析现在通过 task_event 推送 + /tasks 任务列表体现，见 analysisTasks；
   *  保留函数签名是为了不改动既有调用方（useAnalysisHistory 的 loadActiveAnalyses 注入）。 */
  async function loadActiveAnalyses() {
    activeAnalyses.value = {}
  }

  /** 开始轮询：先拉一次未完成任务列表，再按是否有任务调度（WS 已连接时推送为主）。
   *  幂等：重复调用会先停掉旧链，避免两个定时器同时续排。 */
  function startPolling() {
    stopPolling()
    const seq = ++tasksPollSeq
    loadAnalysisTasks()
    scheduleNextPoll(seq)
  }

  function scheduleNextPoll(seq: number) {
    if (seq !== tasksPollSeq) return
    const wasActive = analysisTasks.value.length > 0
    const allPaused =
      analysisTasks.value.length > 0 && analysisTasks.value.every((t) => t.status === 'paused')
    const interval = isWsConnected()
      ? WS_CONNECTED_POLL_MS
      : allPaused
        ? IDLE_POLL_MS
        : wasActive
          ? ACTIVE_POLL_MS
          : IDLE_POLL_MS
    pollTimer = setTimeout(async () => {
      if (seq !== tasksPollSeq) return
      await loadAnalysisTasks()
      if (seq !== tasksPollSeq) return
      const isActive = analysisTasks.value.length > 0
      loadQueue()
      if (isActive || wasActive) {
        options.loadHistory?.()
      }
      if (seq === tasksPollSeq && pollTimer !== null) scheduleNextPoll(seq)
    }, interval)
  }

  /** 停止活动分析轮询 */
  function stopPolling() {
    tasksPollSeq += 1
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  /**
   * 创建批量分析任务并开始轮询（支持可选的多模型 × 多提示词组合参数）
   *
   * @param multi 组合分析参数；缺省为单模型 × 单提示词的普通批量分析
   * @param limit 本次最多分析多少个素材（缺省或 <= 0 表示分析全部未分析素材）；
   *     新后端会把 limit 下推到 /ai/unanalyzed-ids（只返回前 N 个 id），
   *     超出实际未分析数量时按实际数量提交，最终数量以后端返回的 count 为准
   */
  async function triggerBatchAnalyze(multi?: MultiAnalyzeParams, limit?: number) {
    if (batchAnalyzing.value) return
    batchAnalyzing.value = true
    try {
      const requestLimit = typeof limit === 'number' && limit > 0 ? limit : 0
      // limit 下推给后端：新接口只查/返回前 N 个未分析 id，避免前端先拉全量
      const { data } =
        requestLimit > 0
          ? await apiClient.get<{ ids: string[]; count: number }>('/ai/unanalyzed-ids', {
              params: { limit: requestLimit },
            })
          : await apiClient.get<{ ids: string[]; count: number }>('/ai/unanalyzed-ids')
      if (data.count === 0) {
        Message.info('所有素材均已分析过，无需重复分析')
        return
      }
      // 新后端已按 limit 截断；这里的 slice 只兜底旧后端（多返回时仍只提交前 N 个），
      // 不改变最终提交的素材数量。
      const ids = requestLimit > 0 ? data.ids.slice(0, requestLimit) : data.ids
      // 请求体：传了组合参数用对象格式（多模型 × 多提示词），否则保持旧版数组格式
      const isMulti = !!multi
      const body = multi
        ? {
            inspiration_ids: ids,
            models: multi.models,
            prompt_ids: multi.promptIds,
            apply_tags: multi.applyTags,
          }
        : ids
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
        // 后端已创建任务：不能直接 return，否则既不跟踪它、又诱导用户重试产生重复任务
        Message.warning(created.message || 'Ollama 未运行，任务已创建，会在其恢复后自动执行')
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
          // 暂停任务长时间不动：降到空闲频率，别每秒打一次 /tasks/{id}
          const interval =
            batchTask.value.status === 'paused'
              ? IDLE_POLL_MS
              : isWsConnected()
                ? WS_CONNECTED_POLL_MS
                : BATCH_POLL_MS
          batchPollTimer = setTimeout(poll, interval)
        }
      } catch (e) {
        if (seq !== batchPollSeq) return
        // 任务被其它入口删除（pending 取消/任务页删除）：静默停止，不当失败重试
        if ((e as { response?: { status?: number } }).response?.status === 404) {
          stopBatchPolling()
          batchTask.value = null
          return
        }
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

  /** 未知任务事件（例如详情页 / 上传页刚创建的单图分析任务）→ 拉一次任务列表纳入展示。
   *  在途标记避免进度事件密集时把 /tasks 请求放大成风暴。 */
  let tasksRefreshInFlight = false
  async function refreshTasksForUnknownEvent() {
    if (tasksRefreshInFlight) return
    tasksRefreshInFlight = true
    try {
      await loadAnalysisTasks()
    } finally {
      tasksRefreshInFlight = false
    }
  }

  // ── WebSocket 推送合流 ──
  // 1) task_event：与批量轮询同源的任务事件，即时合并 batchTask 进度/状态
  //    （settled 标记保证终态副作用只执行一次，与 useAdminTask 同一套模式）
  const offTaskEvent = subscribeWs('task_event', (raw) => {
    const ev = raw as {
      task_id?: number
      task_type?: string
      status?: string
      progress?: number
      done?: number
      total?: number
      error?: string | null
    }
    if (!ev || !ev.task_id) return
    const current = batchTask.value
    if (current && ev.task_id === current.id) {
      // 已结算：迟到/重复的终态事件直接丢弃，别落到列表分支反复刷 /tasks
      if (batchSettled) return
      if (ev.status && isTaskTerminalStatus(ev.status)) {
        // WS 事件不带 result：终态先到时补拉一次完整快照，否则完成提示退化、
        // batchTask.result 永远为 null。
        apiClient
          .get<TaskInfo>(`/tasks/${current.id}`)
          .then(({ data }) => handleBatchSnapshot(data))
          .catch(() =>
            handleBatchSnapshot({
              ...current,
              status: ev.status as TaskInfo['status'],
              error: ev.error !== undefined ? ev.error : current.error,
            }),
          )
        return
      }
      handleBatchSnapshot({
        ...current,
        status: (ev.status as TaskInfo['status']) ?? current.status,
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
      // 完成即从「分析任务」列表移除（不再展示已完成的批量分析），并刷新统计/历史
      if (isTaskTerminalStatus(row.status)) {
        dropTerminalTasks()
        loadQueue()
        options.loadHistory?.()
      }
      return
    }
    // 列表里还没有该任务（其它页面刚创建的单图/批量任务）→ 拉一次列表纳入展示
    if (ev.task_type === 'batch_analyze' || ev.task_type === 'multi_analyze') {
      void refreshTasksForUnknownEvent()
    }
  })
  // 2) 单图分析完成不再有独立事件：R2 起单图分析就是 batch_analyze(total=1) 任务，
  //    worker 统一广播 task_event，上面已按 task_id 合流；历史/统计由轮询兜底刷新。
  //    （旧的 ai_analysis_done 是 API 内存队列广播，已随该队列删除，不再订阅。）

  /** 单条失败记录重新加入分析队列（后端 /ai/retry 现返回 task_id，同样走 worker 任务队列） */
  async function retryAnalysis(id: string) {
    try {
      const { data } = await apiClient.post<{
        task_id?: number | null
        message?: string
        status?: string
        ollama_will_start?: boolean
      }>(`/ai/retry/${id}`)
      if (data.ollama_will_start) {
        Message.warning(data.message || 'Ollama 正在启动中，任务会在其恢复后自动执行')
      } else {
        Message.success(data.message || '已重新加入队列')
      }
      loadQueue()
      if (typeof data.task_id === 'number') {
        // 重试也是「1 个素材」的 batch_analyze 任务：并入任务列表并接管轮询，
        // 终态提示与历史刷新复用批量任务的既有逻辑
        await loadAnalysisTasks()
        syncActiveTask()
      }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '重试失败'))
    }
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      stopPolling()
      stopBatchPolling()
      offTaskEvent?.()
    })
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
