/** 任务操作（取消 / 删除 / 暂停 / 恢复）：任务中心与采集管理页共用同一套接口与文案。
 *
 * 为什么抽出来：四个操作各自只是「调接口 + 提示 + 刷新」，原先内联在 useTaskCenter 里。
 * 采集管理页要在同一批接口上再做一份「抖音采集历史」（f2_import），重复实现容易让
 * 文案与行为漂移（例如取消了却不刷新、pending 删除后行残留），故收敛到这一个 seam。
 */

import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { UnifiedTask } from '@/types/task'
import { getApiErrorMessage } from '@/utils/apiError'

interface UseTaskActionsOptions {
  /** 队列 pending 任务被物理删除后的回调：调用方据此就地移除该行并纠正页码 */
  onQueueTaskDeleted?: (task: UnifiedTask) => void
  /** 操作成功后的刷新回调（缺省不刷新） */
  reload?: () => void | Promise<void>
}

export function useTaskActions(options: UseTaskActionsOptions = {}) {
  const { onQueueTaskDeleted, reload } = options

  async function cancelTask(task: UnifiedTask) {
    // 队列任务走通用任务接口，采集任务走采集专用接口
    const url =
      task.source === 'queue' ? `/tasks/${task.id}/cancel` : `/scraper/tasks/${task.id}/cancel`
    try {
      const { data } = await apiClient.post<{ message?: string; deleted?: boolean }>(url)
      // 队列任务 pending 取消 = 后端物理删除（deleted: true）；运行中取消仅标记 cancelled
      const deleted = data?.deleted === true
      Message.success(data?.message || (deleted ? '任务已删除' : '已取消'))
      if (deleted) onQueueTaskDeleted?.(task)
      await reload?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  async function deleteTask(task: UnifiedTask) {
    // 采集任务记录在 scraper_tasks 表，走采集专用删除接口；队列任务走通用删除接口
    const url = task.source === 'scraper' ? `/scraper/tasks/${task.id}` : `/tasks/${task.id}`
    try {
      await apiClient.delete(url)
      Message.success('已删除')
      await reload?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  /** 暂停运行中的任务（后端 _PAUSABLE_RUNNING_TYPES：标签网络分析 / 批量·组合分析 / f2 一键获取） */
  async function pauseTask(task: UnifiedTask) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${task.id}/pause`)
      Message.success(data?.message || '任务已暂停')
      await reload?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '暂停失败'))
    }
  }

  /** 恢复已暂停的任务（标签网络分析断点续算；批量分析与 f2 一键获取放回队列幂等续跑） */
  async function resumeTask(task: UnifiedTask) {
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${task.id}/resume`)
      Message.success(data?.message || '任务已恢复')
      await reload?.()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '恢复失败'))
    }
  }

  return { cancelTask, deleteTask, pauseTask, resumeTask }
}
