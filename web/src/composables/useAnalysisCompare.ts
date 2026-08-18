/** AI 分析结果对比 composable：对比弹窗数据加载。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { CompareData } from '@/types/analysis'

export function useAnalysisCompare() {
  const message = useMessage()

  const compareVisible = ref(false)
  const compareLoading = ref(false)
  const compareData = ref<CompareData | null>(null)

  let compareSeq = 0  // 请求序号，防止陈旧响应覆盖新数据

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
      message.error(getApiErrorMessage(e, '获取对比数据失败'))
      compareVisible.value = false
    } finally {
      if (seq === compareSeq) compareLoading.value = false
    }
  }

  return {
    compareVisible,
    compareLoading,
    compareData,
    viewCompare,
  }
}
