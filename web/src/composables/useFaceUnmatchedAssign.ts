/** 未匹配人脸区：列表、分页、勾选、指派人选与批量指派（从 FaceScanView 抽出）。
 *
 * 分层与同页的 useFaceClusterGroups 一致：本 composable 只负责「未匹配人脸 → 指派给
 * 某个人物」这一条链路（加载列表 / 拉人物候选 / 提交指派）；指派成功后如何刷新整页由
 * 视图以回调注入（refreshAll），避免向视图反向依赖。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import { bloggersApi, modelsApi } from '@/api/persons'
import { confirmFaceScan, fetchFaceScanResults, type DetectionItem } from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'

interface FaceUnmatchedAssignDeps {
  /** 指派成功后的整页刷新（视图侧的 refreshAll） */
  refreshAll: () => Promise<void>
}

export function useFaceUnmatchedAssign(opts: FaceUnmatchedAssignDeps) {
  const { refreshAll } = opts

  const unmatchedItems = ref<DetectionItem[]>([])
  const unmatchedPage = ref(1)
  const unmatchedTotal = ref(0)
  const unmatchedLoading = ref(false)
  /** 已勾选待指派的人脸检测 ID */
  const unmatchedChecked = ref<Set<number>>(new Set())
  /** 指派目标类型（博主 / 模特）与目标人物 */
  const assignKind = ref<'blogger' | 'model'>('blogger')
  const assignPersonId = ref<number | undefined>(undefined)
  /** 人物选择候选（博主或模特全量） */
  const assignOptions = ref<Array<{ label: string; value: number }>>([])
  const assignLoading = ref(false)
  const assigning = ref(false)

  async function loadUnmatched() {
    unmatchedLoading.value = true
    try {
      const data = await fetchFaceScanResults({
        status: 'pending',
        unmatched: true,
        page: unmatchedPage.value,
        size: 50,
      })
      unmatchedItems.value = data.items as DetectionItem[]
      unmatchedTotal.value = data.total
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载未匹配人脸失败'))
    } finally {
      unmatchedLoading.value = false
    }
  }

  /** 拉取人物选择候选（博主/模特全量，排除无意义项） */
  async function loadAssignOptions() {
    assignLoading.value = true
    try {
      const api = assignKind.value === 'blogger' ? bloggersApi : modelsApi
      const all: Array<{ id: number; name: string }> = []
      let page = 1
      while (true) {
        const { items, total } = await api.fetchList({ page, size: 200, sort: 'name' })
        all.push(...items)
        if (all.length >= total) break
        page += 1
      }
      assignOptions.value = all.map((p) => ({ label: p.name, value: p.id }))
      assignPersonId.value = undefined
    } catch {
      assignOptions.value = []
    } finally {
      assignLoading.value = false
    }
  }

  /** 未匹配批量指派给某个人物（写关联 + 置 confirmed） */
  async function assignUnmatched() {
    if (unmatchedChecked.value.size === 0) {
      Message.warning('请先勾选要指派的人脸')
      return
    }
    if (!assignPersonId.value) {
      Message.warning('请选择要指派的人物')
      return
    }
    assigning.value = true
    try {
      const result = await confirmFaceScan(
        'confirm',
        [...unmatchedChecked.value].map((id) => ({
          detection_id: id,
          person_type: assignKind.value,
          person_id: assignPersonId.value,
        })),
      )
      Message.success(
        `已指派 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
      )
      unmatchedChecked.value.clear()
      await refreshAll()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '指派失败'))
    } finally {
      assigning.value = false
    }
  }

  return {
    unmatchedItems,
    unmatchedPage,
    unmatchedTotal,
    unmatchedLoading,
    unmatchedChecked,
    assignKind,
    assignPersonId,
    assignOptions,
    assignLoading,
    assigning,
    loadUnmatched,
    loadAssignOptions,
    assignUnmatched,
  }
}
