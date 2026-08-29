/** 通用后台任务轮询 composable：创建任务后轮询进度直至终态。
 *
 * 进度来源双通道：WS task_event 推送（优先，即时）+ 1s 轮询（降级兜底，
 * WS 已连接时放慢到 5s）。终态副作用由 settled 标记保证只执行一次。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { TaskStatus } from '@/types/tagAdvanced'
import { isTaskTerminalStatus } from '@/types/task'
import { subscribeWs, isWsConnected } from '@/composables/useWebSocket'

/** 任务轮询正常间隔（毫秒） */
const POLL_NORMAL_MS = 1000
/** 任务轮询失败重试间隔（毫秒） */
const POLL_RETRY_MS = 3000
/** WS 已连接时的保底轮询间隔（毫秒）：推送驱动为主，低频轮询兜底 */
const WS_CONNECTED_POLL_MS = 5000

export function useTaskPolling() {
  const task = ref<TaskStatus | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let seq = 0 // 代际号：stop/重启时自增，使在途请求返回后不再续排
  let settled = false // 终态幂等标记：WS 推送与轮询合流后，终态副作用只执行一次
  let currentOnDone: ((result: Record<string, unknown> | null) => void) | null = null

  function stopPolling() {
    seq += 1
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  /**
   * 统一处理一次任务快照（轮询返回 / WS 推送合流后共用）：更新状态 + 终态副作用。
   * 成功时以任务结果调用 onDone(result)；失败/取消自动提示；settled 保证幂等。
   */
  function handleSnapshot(
    data: TaskStatus,
    onDone: (result: Record<string, unknown> | null) => void,
  ) {
    task.value = data
    if (!isTaskTerminalStatus(data.status)) return
    if (settled) return
    settled = true
    stopPolling()
    if (data.status === 'success') {
      onDone(data.result)
    } else if (data.status === 'failed') {
      Message.error(`任务失败：${data.error || '未知错误'}`)
    } else {
      Message.info('任务已取消')
    }
  }

  /**
   * 轮询任务直至终态（约 1 秒一次；WS 已连接时 5 秒兜底）。
   * 成功时以任务结果调用 onDone(result)；失败/取消自动提示。
   */
  function pollTask(taskId: number, onDone: (result: Record<string, unknown> | null) => void) {
    stopPolling()
    settled = false
    currentOnDone = onDone
    const mySeq = seq
    let consecutiveFailures = 0

    const poll = async () => {
      if (mySeq !== seq) return
      try {
        const { data } = await apiClient.get<TaskStatus>(`/tasks/${taskId}`)
        if (mySeq !== seq) return
        consecutiveFailures = 0
        handleSnapshot(data, onDone)
        if (task.value && !isTaskTerminalStatus(task.value.status)) {
          timer = setTimeout(poll, isWsConnected() ? WS_CONNECTED_POLL_MS : POLL_NORMAL_MS)
        }
      } catch {
        if (mySeq !== seq) return
        consecutiveFailures += 1
        if (consecutiveFailures >= 5) {
          stopPolling()
          Message.error('获取任务状态多次失败，已停止轮询，请稍后手动刷新')
          return
        }
        timer = setTimeout(poll, POLL_RETRY_MS)
      }
    }
    poll()
  }

  // WS 推送合流：收到当前跟踪任务的 task_event 时即时合并进度/状态，
  // 终态事件与轮询返回走同一处理（settled 标记保证副作用只执行一次）
  subscribeWs('task_event', (raw) => {
    const ev = raw as {
      task_id?: number
      status?: string
      progress?: number
      done?: number
      total?: number
      error?: string | null
      result?: Record<string, unknown> | null
    }
    const current = task.value
    if (!current || !ev || ev.task_id !== current.id || settled) return
    handleSnapshot(
      {
        ...current,
        status: (ev.status as TaskStatus['status']) ?? current.status,
        progress: typeof ev.progress === 'number' ? ev.progress : current.progress,
        done: typeof ev.done === 'number' ? ev.done : current.done,
        total: typeof ev.total === 'number' ? ev.total : current.total,
        error: ev.error !== undefined ? ev.error : current.error,
        result: ev.result !== undefined ? ev.result : current.result,
      },
      (result) => currentOnDone?.(result),
    )
  })

  return { task, pollTask, stopPolling }
}
