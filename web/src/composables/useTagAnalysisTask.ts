/** 标签分析类后台任务的提交 + 轮询编排（健康度扫描 / 聚类扫描 / 网络图分析）。
 *
 * 三个面板此前都重复「提交任务 → pollTask → asXxxResult 转型 → 处理结果 →
 * 维护 scanning/running 状态」的样板。本 composable 用泛型收敛这套流程：
 *
 * ```ts
 * const { run, running } = useTagAnalysisTask({
 *   submit: () => scanClusters({ threshold }),
 *   transform: asClusterResult,
 *   onDone: (r) => { groups.value = r.groups },
 * })
 * ```
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'
import { useTaskPolling } from './useTaskPolling'

interface SubmitResult {
  task_id: number
}

interface Options<TResult> {
  /** 提交后台任务，返回 task_id（参数由调用方闭包捕获） */
  submit: () => Promise<SubmitResult>
  /** 把任务原始 result 安全转型为业务结果；返回 null 表示结构不符 */
  transform: (raw: Record<string, unknown> | null) => TResult | null
  /** 任务成功且转型通过后的回调（面板在此写入自身状态） */
  onDone?: (result: TResult) => void
  /** 提交阶段失败的回调（轮询失败由 useTaskPolling 统一提示） */
  onError?: (e: unknown) => void
}

export function useTagAnalysisTask<TResult>(opts: Options<TResult>) {
  const { task, pollTask, stopPolling } = useTaskPolling()
  const submitting = ref(false)
  const result = ref<TResult | null>(null)

  const running = computed(
    () =>
      submitting.value || Boolean(task.value && ['pending', 'running'].includes(task.value.status)),
  )

  /** 任务是否处于已暂停（暂停后轮询继续，等待恢复或终态） */
  const paused = computed(() => task.value?.status === 'paused')

  /** 暂停运行中的任务（后端仅 tag_network_analyze 支持，其余类型返回 400） */
  async function pause() {
    if (!task.value || task.value.status !== 'running') return
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${task.value.id}/pause`)
      Message.success(data?.message || '任务已暂停')
      // 轮询仍在继续，下一轮即会拉到 paused 状态
    } catch (e) {
      Message.error(getApiErrorMessage(e, '暂停失败'))
    }
  }

  /** 恢复已暂停的任务（断点续算） */
  async function resume() {
    if (!task.value || task.value.status !== 'paused') return
    try {
      const { data } = await apiClient.post<{ message?: string }>(`/tasks/${task.value.id}/resume`)
      Message.success(data?.message || '任务已恢复')
    } catch (e) {
      Message.error(getApiErrorMessage(e, '恢复失败'))
    }
  }

  /** 提交任务并开始轮询；重复调用（运行中）会被忽略 */
  async function run() {
    if (running.value) return
    submitting.value = true
    try {
      const { task_id } = await opts.submit()
      pollTask(task_id, (raw) => {
        const r = opts.transform(raw)
        if (r) {
          result.value = r
          opts.onDone?.(r)
        }
      })
    } catch (e) {
      opts.onError?.(e)
    } finally {
      submitting.value = false
    }
  }

  return { run, running, paused, pause, resume, result, task, stopPolling }
}
