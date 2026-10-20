/** 人脸扫描的任务控制动作（从 FaceScanView.vue 抽出，C-2）。
 *
 * 覆盖：开始扫描 / 取消（扫描或匹配）/ 开始匹配 / 轮询到任务结束。
 * 任务状态与勾选集合归视图所有（模板要读），这里以 opts 传入。
 */

import { Message } from '@arco-design/web-vue'
import type { Ref } from 'vue'
import type { FaceScanTaskOut } from '@/api/faceScan'
import { fetchFaceScanTask, runFaceMatch, startFaceScan } from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'
import { pollTaskUntilIdle } from '@/utils/taskPollUntilIdle'

interface FaceScanActionDeps {
  scanTask: Ref<FaceScanTaskOut | null>
  matchTask: Ref<FaceScanTaskOut | null>
  scope: Ref<'incremental' | 'semi' | 'all'>
  autoMatch: Ref<boolean>
  starting: Ref<boolean>
  cancelling: Ref<boolean>
  matching: Ref<boolean>
  detailChecked: Ref<Set<number>>
  refreshTasks: () => Promise<void>
  refreshAll: () => Promise<void>
}

export function useFaceScanActions(opts: FaceScanActionDeps) {
  const {
    scanTask,
    matchTask,
    scope,
    autoMatch,
    starting,
    cancelling,
    matching,
    detailChecked,
    refreshTasks,
    refreshAll,
  } = opts

  async function startScan() {
    starting.value = true
    try {
      const { task_id, total } = await startFaceScan(scope.value, autoMatch.value)
      Message.success(`扫描任务已创建（待扫 ${total} 个素材）`)
      detailChecked.value.clear()
      await refreshTasks()
      void pollUntilIdle(task_id)
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
  async function startMatch() {
    matching.value = true
    try {
      const { task_id } = await runFaceMatch({ scope: 'all' })
      Message.success('全库重匹配任务已创建')
      await refreshTasks()
      void pollUntilIdle(task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建匹配任务失败'))
    } finally {
      matching.value = false
    }
  }

  /** 轮询扫描/匹配任务直到终态（3s 间隔；任务完成后刷新结果区） */
  async function pollUntilIdle(taskId: number) {
    await pollTaskUntilIdle({
      taskId,
      intervalMs: 3000,
      refresh: async () => {
        const status = await fetchFaceScanTask()
        scanTask.value = status.scan_task
        matchTask.value = status.match_task
        return [status.scan_task, status.match_task].find((t) => t?.id === taskId) ?? null
      },
      onIdle: refreshAll,
    })
  }

  // ── 结果区：聚合（按人物）──
  /** 结果区 tab：待审核候选 / 已确认 / 未匹配人脸 / 聚合分组 */

  return { startScan, cancelTask, startMatch, pollUntilIdle }
}
