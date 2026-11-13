/** 质量审核「未通过素材」列表与动作：把 ReviewPanel 里的 API 调用收口。
 *
 * 组件保留模板、提示与弹窗状态；本 composable 负责分页、筛选、陈旧响应防护与
 * 审核动作的请求。
 */

import { ref } from 'vue'
import apiClient from '@/api/client'
import { submitQualityCorrection, type QualityCorrectionPayload } from '@/api/aiQuality'
import { batchTrash, batchUpdateInspirations, deleteRejectedInspirations } from '@/api/inspirations'

export interface RejectedItem {
  quality_status?: string | null
  id: string
  thumbnail_path?: string | null
  file_path: string
  quality_reason?: string | null
  source_type: string
}

const PAGE_SIZE = 100

export function useRejectedInspirations() {
  const items = ref<RejectedItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const page = ref(1)
  /** 按原因筛选未通过素材（空串=全部） */
  const reasonFilter = ref('')
  let seq = 0

  async function load(reset = true) {
    if (reset) page.value = 1
    const current = page.value
    loading.value = true
    const currentSeq = ++seq
    try {
      const { data } = await apiClient.get<{ items: RejectedItem[]; total: number }>(
        '/inspirations',
        {
          params: {
            quality_status: 'rejected',
            size: PAGE_SIZE,
            page: current,
            sort: 'newest',
            ...(reasonFilter.value ? { quality_reason_code: reasonFilter.value } : {}),
          },
        },
      )
      if (currentSeq !== seq) return // 忽略过期响应
      items.value = reset ? data.items : [...items.value, ...data.items]
      total.value = data.total
    } catch {
      /* 静默 */
    } finally {
      if (currentSeq === seq) loading.value = false
    }
  }

  function loadMore() {
    if (loading.value) return // 防止加载中重复点击导致跳页
    page.value += 1
    void load(false)
  }

  /** 单张通过：成功后从当前列表移除并同步 total */
  async function approve(id: string) {
    await apiClient.patch(`/inspirations/${id}`, { quality_status: 'approved' })
    items.value = items.value.filter((i) => i.id !== id)
    total.value = Math.max(0, total.value - 1)
  }

  /** 全部已拒绝移入垃圾桶 */
  async function trashAllRejected() {
    const data = await deleteRejectedInspirations()
    items.value = []
    total.value = 0
    return data
  }

  async function batchApprove(ids: string[]) {
    return batchUpdateInspirations(ids, { quality_status: 'approved' })
  }

  async function batchTrashSelected(ids: string[]) {
    return batchTrash(ids, '质量差', 'auto')
  }

  async function submitCorrection(payload: QualityCorrectionPayload) {
    return submitQualityCorrection(payload)
  }

  return {
    items,
    total,
    loading,
    page,
    reasonFilter,
    load,
    loadMore,
    approve,
    trashAllRejected,
    batchApprove,
    batchTrashSelected,
    submitCorrection,
  }
}
