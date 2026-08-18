/** AI 分析详情 composable：详情弹窗数据加载。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import type { AnalysisDetail } from '@/types/analysis'

export function useAnalysisDetail() {
  const message = useMessage()

  const detailVisible = ref(false)
  const detailLoading = ref(false)
  const currentDetail = ref<AnalysisDetail | null>(null)

  let detailSeq = 0  // 请求序号，防止陈旧响应覆盖新数据

  /** 加载单条分析详情 */
  async function viewDetail(logId: number) {
    detailVisible.value = true
    detailLoading.value = true
    currentDetail.value = null
    const seq = ++detailSeq
    try {
      const { data } = await apiClient.get<AnalysisDetail>(`/ai/history/${logId}`)
      if (seq !== detailSeq) return
      currentDetail.value = data
    } catch (e) {
      if (seq !== detailSeq) return
      message.error(getApiErrorMessage(e, '获取详情失败'))
      detailVisible.value = false
    } finally {
      if (seq === detailSeq) detailLoading.value = false
    }
  }

  return {
    detailVisible,
    detailLoading,
    currentDetail,
    viewDetail,
  }
}
