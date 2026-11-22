/** 采集任务日志域：日志查看状态与加载。 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'

/** 任务日志查看器状态与操作，由 ScraperView 消费。 */
export function useScraperLog() {
  const logTaskId = ref<number | null>(null)
  const logContent = ref('')
  const logLoading = ref(false)

  /** 打开/收起日志：再次点击同一任务则收起 */
  async function viewLog(taskId: number) {
    if (logTaskId.value === taskId) {
      logTaskId.value = null
      logContent.value = ''
      logLoading.value = false
      return
    }
    logTaskId.value = taskId
    logLoading.value = true
    try {
      const content = (await apiClient.get(`/scraper/tasks/${taskId}/log`)).data.content
      // 竞态防护：快速切换任务时，旧任务的日志响应不覆盖新任务
      if (logTaskId.value === taskId) {
        logContent.value = content
        logLoading.value = false
      }
    } catch {
      // 只有当前任务失败才提示并收起；旧任务的失败静默丢弃（不干扰已打开的新任务）
      if (logTaskId.value === taskId) {
        Message.error('日志加载失败')
        logTaskId.value = null
        logLoading.value = false
      }
    }
  }

  /** 关闭日志查看器 */
  function closeLog() {
    logTaskId.value = null
    logContent.value = ''
    logLoading.value = false
  }

  return { logTaskId, logContent, logLoading, viewLog, closeLog }
}
