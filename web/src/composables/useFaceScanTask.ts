/** 人脸扫描任务管理：扫描任务、匹配任务的启动、取消、轮询。 */

import { Message } from '@arco-design/web-vue'
import { computed, ref } from 'vue'
import {
  fetchFaceScanTask,
  runFaceMatch,
  startFaceScan,
  type FaceScanTaskOut,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'

/** 扫描模式：增量/半增量/全量 */
export type ScanScope = 'incremental' | 'semi' | 'all'

export interface FaceScanTaskState {
  scanTask: FaceScanTaskOut | null
  matchTask: FaceScanTaskOut | null
  scope: ScanScope
  autoMatch: boolean
  starting: boolean
  cancelling: boolean
  matching: boolean
  busy: boolean
}

export function useFaceScanTask(clusterTask?: () => FaceScanTaskOut | null) {
  const scanTask = ref<FaceScanTaskOut | null>(null)
  const matchTask = ref<FaceScanTaskOut | null>(null)
  const scope = ref<ScanScope>('semi')
  const autoMatch = ref(false)
  const starting = ref(false)
  const cancelling = ref(false)
  const matching = ref(false)

  /** 是否有任务在运行（决定轮询与按钮态） */
  const busy = computed(() => {
    const cluster = clusterTask?.()
    return (
      scanTask.value?.status === 'running' ||
      scanTask.value?.status === 'pending' ||
      matchTask.value?.status === 'running' ||
      matchTask.value?.status === 'pending' ||
      cluster?.status === 'running' ||
      cluster?.status === 'pending'
    )
  })

  /** 刷新任务状态 */
  async function refreshTasks() {
    try {
      const status = await fetchFaceScanTask()
      scanTask.value = status.scan_task
      matchTask.value = status.match_task
    } catch (e) {
      Message.error(getApiErrorMessage(e, '获取任务状态失败'))
    }
  }

  /** 开始扫描（增量/全量） */
  async function startScan(onRefresh?: () => Promise<void>) {
    starting.value = true
    try {
      const { task_id, total } = await startFaceScan(scope.value, autoMatch.value)
      Message.success(`扫描任务已创建（待扫 ${total} 个素材）`)
      await refreshTasks()
      if (onRefresh) await onRefresh()
      void pollUntilIdle(task_id, onRefresh)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建扫描任务失败'))
    } finally {
      starting.value = false
    }
  }

  /** 取消任务（运行中的人脸任务也可取消，增量续跑） */
  async function cancelTask() {
    const task = scanTask.value ?? matchTask.value
    if (!task) return
    cancelling.value = true
    try {
      const { data } = await import('@/api/client').then((m) =>
        m.default.post(`/tasks/${task.id}/cancel`),
      )
      Message.success((data as { message?: string }).message || '任务已取消')
      await refreshTasks()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    } finally {
      cancelling.value = false
    }
  }

  /** 全库重匹配（不动 GPU，秒级~分钟级） */
  async function startMatch(onRefresh?: () => Promise<void>) {
    matching.value = true
    try {
      const { task_id } = await runFaceMatch({ scope: 'all' })
      Message.success('全库重匹配任务已创建')
      await refreshTasks()
      if (onRefresh) await onRefresh()
      void pollUntilIdle(task_id, onRefresh)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建匹配任务失败'))
    } finally {
      matching.value = false
    }
  }

  /** 轮询任务直到终态（3s 间隔；任务完成后刷新结果区） */
  async function pollUntilIdle(taskId: number, onRefresh?: () => Promise<void>) {
    while (true) {
      await new Promise((r) => setTimeout(r, 3000))
      await refreshTasks()
      const current = [scanTask.value, matchTask.value].find((t) => t?.id === taskId)
      if (!current || !['running', 'pending'].includes(current.status)) {
        if (onRefresh) await onRefresh()
        return
      }
    }
  }

  return {
    // 状态
    scanTask,
    matchTask,
    scope,
    autoMatch,
    starting,
    cancelling,
    matching,
    busy,
    // 方法
    refreshTasks,
    startScan,
    cancelTask,
    startMatch,
    pollUntilIdle,
  }
}
