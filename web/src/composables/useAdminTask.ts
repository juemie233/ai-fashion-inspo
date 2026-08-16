/** 后台任务轮询 composable：批量删除/去重的进度查询、恢复与停止。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { AdminTask } from '@/types/admin'

export function useAdminTask() {
  const message = useMessage()
  const adminTask = ref<AdminTask | null>(null)
  let adminPollTimer: ReturnType<typeof setTimeout> | null = null
  let adminPollSeq = 0  // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排

  function stopAdminPolling() {
    adminPollSeq += 1  // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
    if (adminPollTimer) { clearTimeout(adminPollTimer); adminPollTimer = null }
  }

  /** 轮询后台任务状态（约 1 秒一次），完成后执行 onDone 回调 */
  function startAdminPolling(taskId: number, onDone: () => void) {
    stopAdminPolling()
    const seq = adminPollSeq  // 当前代际：stopAdminPolling 已自增，旧链的 seq 与之不符即失效
    let consecutiveFailures = 0  // 连续失败次数，失败时有限次重试而非直接停止
    const poll = async () => {
      if (seq !== adminPollSeq) return  // 已被 stop/新轮询取代，不再调度
      try {
        const { data } = await apiClient.get<AdminTask>(`/tasks/${taskId}`)
        if (seq !== adminPollSeq) return  // 在途请求返回前已被停止，丢弃结果
        consecutiveFailures = 0
        adminTask.value = data
        if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
          stopAdminPolling()
          if (data.status === 'success') {
            onDone()
          } else if (data.status === 'failed') {
            message.error(`任务失败：${data.error || '未知错误'}`)
          } else {
            message.info('任务已取消')
          }
          return
        }
        adminPollTimer = setTimeout(poll, 1000)
      } catch {
        if (seq !== adminPollSeq) return
        consecutiveFailures += 1
        if (consecutiveFailures >= 5) {
          // 连续多次失败才停止，避免后端重启/网络抖动导致任务进度卡死
          stopAdminPolling()
          message.error('获取任务状态多次失败，已停止轮询，请稍后手动刷新')
          return
        }
        // 有限次重试：间隔放大到 3 秒，继续续排轮询链
        adminPollTimer = setTimeout(poll, 3000)
      }
    }
    poll()
  }

  /** 恢复进行中的后台任务：刷新页面后查询是否有 pending/running 的删除/去重/向量回填任务并继续轮询 */
  async function resumeAdminTask(onDone: () => void) {
    try {
      const { data } = await apiClient.get<{ items: AdminTask[] }>('/tasks', { params: { size: 20 } })
      const active = data.items.find((t) =>
        (t.type === 'batch_delete' || t.type === 'deduplicate' || t.type === 'vector_backfill') &&
        (t.status === 'pending' || t.status === 'running')
      )
      if (active) {
        adminTask.value = active
        startAdminPolling(active.id, onDone)
      }
    } catch { /* 静默 */ }
  }

  return { adminTask, startAdminPolling, stopAdminPolling, resumeAdminTask }
}
