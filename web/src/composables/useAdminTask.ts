/** 后台任务轮询 composable：批量删除/去重的进度查询、恢复与停止。
 *
 * 进度来源双通道：WS task_event 推送（优先，即时）+ 1s 轮询（降级兜底，
 * WS 已连接时放慢到 5s）。终态处理（完成回调/失败提示）两条通道共用同一逻辑。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { AdminTask } from '@/types/admin'
import { subscribeWs, isWsConnected } from '@/composables/useWebSocket'

/** 任务轮询正常间隔（毫秒）：约 1 秒一次 */
const POLL_NORMAL_MS = 1000
/** 任务轮询失败重试间隔（毫秒）：放大到 3 秒，有限次重试 */
const POLL_RETRY_MS = 3000
/** WS 已连接时的保底轮询间隔（毫秒）：推送驱动为主，低频轮询兜底 */
const WS_CONNECTED_POLL_MS = 5000

export function useAdminTask() {
  const adminTask = ref<AdminTask | null>(null)
  let adminPollTimer: ReturnType<typeof setTimeout> | null = null
  let adminPollSeq = 0 // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排
  let settled = false // 终态幂等标记：WS 推送与轮询合流后，终态副作用只执行一次
  let currentOnDone: (() => void) | null = null

  function stopAdminPolling() {
    adminPollSeq += 1 // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
    if (adminPollTimer) {
      clearTimeout(adminPollTimer)
      adminPollTimer = null
    }
  }

  /** 轮询后台任务状态（约 1 秒一次；WS 已连接时 5 秒兜底），完成后执行 onDone 回调 */
  function startAdminPolling(taskId: number, onDone: () => void) {
    stopAdminPolling()
    settled = false
    currentOnDone = onDone
    const seq = adminPollSeq // 当前代际：stopAdminPolling 已自增，旧链的 seq 与之不符即失效
    let consecutiveFailures = 0 // 连续失败次数，失败时有限次重试而非直接停止
    const poll = async () => {
      if (seq !== adminPollSeq) return // 已被 stop/新轮询取代，不再调度
      try {
        const { data } = await apiClient.get<AdminTask>(`/tasks/${taskId}`)
        if (seq !== adminPollSeq) return // 在途请求返回前已被停止，丢弃结果
        consecutiveFailures = 0
        handleSnapshot(data)
        if (
          adminTask.value &&
          (adminTask.value.status === 'pending' || adminTask.value.status === 'running')
        ) {
          const interval = isWsConnected() ? WS_CONNECTED_POLL_MS : POLL_NORMAL_MS
          adminPollTimer = setTimeout(poll, interval)
        }
      } catch {
        if (seq !== adminPollSeq) return
        consecutiveFailures += 1
        if (consecutiveFailures >= 5) {
          // 连续多次失败才停止，避免后端重启/网络抖动导致任务进度卡死
          stopAdminPolling()
          Message.error('获取任务状态多次失败，已停止轮询，请稍后手动刷新')
          return
        }
        // 有限次重试：间隔放大到 3 秒，继续续排轮询链
        adminPollTimer = setTimeout(poll, POLL_RETRY_MS)
      }
    }
    poll()
  }

  /**
   * 统一处理一次任务快照（轮询返回 / WS 推送合流后共用）：更新状态 + 终态副作用。
   * settled 幂等标记保证终态副作用（onDone/提示）只执行一次——WS 推送与轮询
   * 谁先到谁触发，后到的一条因 settled 已置位被跳过。
   */
  function handleSnapshot(data: AdminTask) {
    adminTask.value = data
    if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
      if (settled) return
      settled = true
      stopAdminPolling()
      if (data.status === 'success') {
        currentOnDone?.()
      } else if (data.status === 'failed') {
        Message.error(`任务失败：${data.error || '未知错误'}`)
      } else {
        Message.info('任务已取消')
      }
    }
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
    }
    const current = adminTask.value
    if (!current || !ev || ev.task_id !== current.id || settled) return
    handleSnapshot({
      ...current,
      status: ev.status ?? current.status,
      progress: typeof ev.progress === 'number' ? ev.progress : current.progress,
      done: typeof ev.done === 'number' ? ev.done : current.done,
      total: typeof ev.total === 'number' ? ev.total : current.total,
      error: ev.error !== undefined ? ev.error : current.error,
    })
  })

  /** 恢复进行中的后台任务：刷新页面后查询是否有 pending/running 的删除/去重/向量回填任务并继续轮询 */
  async function resumeAdminTask(onDone: () => void) {
    try {
      const { data } = await apiClient.get<{ items: AdminTask[] }>('/tasks', {
        params: { size: 20 },
      })
      const active = data.items.find(
        (t) =>
          (t.type === 'batch_delete' || t.type === 'deduplicate' || t.type === 'vector_backfill') &&
          (t.status === 'pending' || t.status === 'running'),
      )
      if (active) {
        adminTask.value = active
        startAdminPolling(active.id, onDone)
      }
    } catch {
      /* 静默 */
    }
  }

  return { adminTask, startAdminPolling, stopAdminPolling, resumeAdminTask }
}
