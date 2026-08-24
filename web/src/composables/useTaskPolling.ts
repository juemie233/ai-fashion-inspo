/** 通用后台任务轮询 composable：创建任务后轮询进度直至终态。 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { TaskStatus } from '@/types/tagAdvanced'

/** 任务轮询正常间隔（毫秒） */
const POLL_NORMAL_MS = 1000
/** 任务轮询失败重试间隔（毫秒） */
const POLL_RETRY_MS = 3000

export function useTaskPolling() {
  const task = ref<TaskStatus | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let seq = 0 // 代际号：stop/重启时自增，使在途请求返回后不再续排

  function stopPolling() {
    seq += 1
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  /**
   * 轮询任务直至终态。
   * 成功时以任务结果调用 onDone(result)；失败/取消自动提示。
   */
  function pollTask(taskId: number, onDone: (result: Record<string, unknown> | null) => void) {
    stopPolling()
    const mySeq = seq
    let consecutiveFailures = 0

    const poll = async () => {
      if (mySeq !== seq) return
      try {
        const { data } = await apiClient.get<TaskStatus>(`/tasks/${taskId}`)
        if (mySeq !== seq) return
        consecutiveFailures = 0
        task.value = data
        if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
          stopPolling()
          if (data.status === 'success') {
            onDone(data.result)
          } else if (data.status === 'failed') {
            Message.error(`任务失败：${data.error || '未知错误'}`)
          } else {
            Message.info('任务已取消')
          }
          return
        }
        timer = setTimeout(poll, POLL_NORMAL_MS)
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

  return { task, pollTask, stopPolling }
}
