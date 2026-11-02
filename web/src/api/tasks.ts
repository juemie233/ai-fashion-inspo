/** 任务运行台账（后端 GET /api/tasks/{id}/runs）：每次执行尝试一行。 */

import apiClient from './client'
import type { TaskRunOut } from '@/types/task'

/**
 * 拉取某任务的运行台账（升序，最多 200 条）。
 *
 * 台账由 worker 写 ``task_run_logs``：谁在什么时间跑了多久、处理多少、用的哪张卡。
 * 任务被删除/重试都不会丢记录，因此这里以任务 id 过滤，不要求任务仍在队列里。
 */
export async function fetchTaskRuns(taskId: number): Promise<TaskRunOut[]> {
  const { data } = await apiClient.get<TaskRunOut[]>(`/tasks/${taskId}/runs`)
  return Array.isArray(data) ? data : []
}
