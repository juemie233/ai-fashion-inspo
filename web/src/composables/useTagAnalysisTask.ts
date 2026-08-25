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

  return { run, running, result, task, stopPolling }
}
