/** 近似重复面板的 API 层：扫描、哈希补齐任务与批量删除提交。
 *
 * 组件只保留对比弹窗与决策状态；请求/响应类型统一在这里收口。
 */

import apiClient from '@/api/client'
import {
  fetchNearDuplicates,
  startPhashBackfill,
  type NearDuplicateFile,
  type NearDuplicateGroup,
  type NearDuplicateResult,
} from '@/api/admin'

export type { NearDuplicateFile, NearDuplicateGroup, NearDuplicateResult }

interface PhashBackfillTask {
  status: string
  progress: number
  done: number
  total: number
  result: { complete?: boolean; unhashable?: number; missing?: number } | null
}

interface BatchDeleteResult {
  message: string
  task_id: number
}

export function useNearDuplicatesApi() {
  return {
    scan: (limit: number, threshold: number): Promise<NearDuplicateResult> =>
      fetchNearDuplicates(limit, threshold),
    startBackfill: (forceRehash: boolean) => startPhashBackfill(forceRehash),
    fetchBackfillTask: async (taskId: number): Promise<PhashBackfillTask> => {
      const { data } = await apiClient.get<PhashBackfillTask>(`/tasks/${taskId}`)
      return data
    },
    submitBatchDelete: async (ids: string[]): Promise<BatchDeleteResult> => {
      const { data } = await apiClient.post<BatchDeleteResult>('/admin/batch-delete', { ids })
      return data
    },
  }
}
