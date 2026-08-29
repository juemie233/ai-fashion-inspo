/** AI 分析结果对比 composable：对比弹窗数据加载（按素材全量对比 + 按勾选记录批量对比）。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { CompareData, CompareBatchData } from '@/types/analysis'

export function useAnalysisCompare() {
  const compareVisible = ref(false)
  const compareLoading = ref(false)
  const compareData = ref<CompareData | null>(null)

  // 批量对比（勾选多条记录）
  const compareBatchVisible = ref(false)
  const compareBatchLoading = ref(false)
  const compareBatchData = ref<CompareBatchData | null>(null)

  let compareSeq = 0 // 请求序号，防止陈旧响应覆盖新数据
  let compareBatchSeq = 0

  /** 加载某个素材的分析结果对比数据 */
  async function viewCompare(inspirationId: string) {
    compareVisible.value = true
    compareLoading.value = true
    compareData.value = null
    const seq = ++compareSeq
    try {
      const { data } = await apiClient.get<CompareData>(`/ai/compare/${inspirationId}`)
      if (seq !== compareSeq) return
      compareData.value = data
    } catch (e) {
      if (seq !== compareSeq) return
      Message.error(getApiErrorMessage(e, '获取对比数据失败'))
      compareVisible.value = false
    } finally {
      if (seq === compareSeq) compareLoading.value = false
    }
  }

  /** 按勾选的日志 ID 批量对比（需为同一素材的多条记录） */
  async function viewCompareBatch(logIds: number[]) {
    if (logIds.length < 2) {
      Message.warning('请至少勾选 2 条记录进行对比')
      return
    }
    compareBatchVisible.value = true
    compareBatchLoading.value = true
    compareBatchData.value = null
    const seq = ++compareBatchSeq
    try {
      const { data } = await apiClient.post<CompareBatchData>('/ai/compare-batch', {
        log_ids: logIds,
      })
      if (seq !== compareBatchSeq) return
      compareBatchData.value = data
    } catch (e) {
      if (seq !== compareBatchSeq) return
      Message.error(getApiErrorMessage(e, '批量对比失败'))
      compareBatchVisible.value = false
    } finally {
      if (seq === compareBatchSeq) compareBatchLoading.value = false
    }
  }

  return {
    compareVisible,
    compareLoading,
    compareData,
    viewCompare,
    compareBatchVisible,
    compareBatchLoading,
    compareBatchData,
    viewCompareBatch,
  }
}
