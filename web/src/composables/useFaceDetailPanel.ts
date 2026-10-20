/** 人脸扫描页的「人物明细」面板（从 FaceScanView.vue 抽出，F 批）。
 *
 * 职责：某个待审核人物展开后的明细列表（分页）+ 勾选 + 批量确认/驳回，
 * 以及「按人物一键审核全部候选（可只审高置信度）」。
 *
 * 为什么抽：视图 812 行，而这段是自成体系的状态 + 逻辑（按 CLAUDE.md 应放 composables）；
 * 视图只保留「勾谁、点哪个按钮」的接线，模板一行不用改。
 *
 * 结果区 tab 决定操作目标（pending → 确认关联 / confirmed → 驳回撤销），
 * 所以 `resultTab` 由视图传入而不是本模块自持。
 */

import { computed, ref, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import {
  confirmFaceScan,
  fetchFaceScanResults,
  type DetectionItem,
  type PersonAggregateItem,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'

/** 结果区四个 tab（视图侧也用它做 query 校验，故在此导出，避免两处各写一份） */
export type ResultTab = 'pending' | 'confirmed' | 'unmatched' | 'cluster'

/** 明细批量操作的目标状态：pending 页面里操作 = 确认，confirmed 页面里 = 驳回 */
type TargetStatus = 'pending' | 'confirmed'

/** 高置信度快捷确认的门槛（与视图上的「≥0.75 快捷确认」文案一致） */
const HIGH_CONFIDENCE = 0.75

interface FaceDetailPanelDeps {
  /** 当前结果区 tab（决定确认/驳回的目标状态） */
  resultTab: Ref<ResultTab>
  /** 审核动作完成后刷新整个页面数据（聚合区 + 任务状态） */
  refreshAll: () => Promise<void>
}

export function useFaceDetailPanel(opts: FaceDetailPanelDeps) {
  const { resultTab, refreshAll } = opts

  const detailKey = ref('') // `${person_type}:${person_id}`
  const detailItems = ref<DetectionItem[]>([])
  const detailPage = ref(1)
  const detailTotal = ref(0)
  const detailLoading = ref(false)
  const detailChecked = ref<Set<number>>(new Set())
  const detailActionBusy = ref(false)

  const selectedPerson = computed(() => {
    const [type, id] = detailKey.value.split(':')
    return { personType: type as 'blogger' | 'model', personId: Number(id) }
  })

  const targetStatus = (): TargetStatus =>
    resultTab.value === 'confirmed' ? 'confirmed' : 'pending'

  /** 展开/收起某人物明细 */
  async function toggleDetail(person: PersonAggregateItem) {
    const key = `${person.person_type}:${person.person_id}`
    if (detailKey.value === key) {
      detailKey.value = ''
      detailChecked.value = new Set()
      return
    }
    detailKey.value = key
    detailChecked.value = new Set()
    detailPage.value = 1
    await loadDetail()
  }

  async function loadDetail() {
    if (!detailKey.value) return
    detailLoading.value = true
    try {
      const { personType, personId } = selectedPerson.value
      const data = await fetchFaceScanResults({
        status: targetStatus(),
        person_type: personType,
        person_id: personId,
        page: detailPage.value,
        size: 50,
      })
      detailItems.value = data.items as DetectionItem[]
      detailTotal.value = data.total
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载明细失败'))
    } finally {
      detailLoading.value = false
    }
  }

  /** 拉取某人物全部明细（分页循环，供批量审核） */
  async function fetchAllDetections(
    status: TargetStatus,
    personType: 'blogger' | 'model',
    personId: number,
  ): Promise<DetectionItem[]> {
    const all: DetectionItem[] = []
    let page = 1
    while (true) {
      const data = await fetchFaceScanResults({
        status,
        person_type: personType,
        person_id: personId,
        page,
        size: 200,
      })
      all.push(...(data.items as DetectionItem[]))
      if (all.length >= data.total) break
      page += 1
    }
    return all
  }

  /** 批量审核当前人物的候选（confirm/reject），items 为空 = 全部 */
  async function actOnPerson(
    person: PersonAggregateItem,
    action: 'confirm' | 'reject',
    onlyHighConfidence = false,
  ) {
    detailActionBusy.value = true
    try {
      const { person_type: personType, person_id: personId } = person
      const all = await fetchAllDetections(targetStatus(), personType, personId)
      const items = all
        .filter((d) => !onlyHighConfidence || (d.confidence ?? 0) >= HIGH_CONFIDENCE)
        .map((d) => ({
          detection_id: d.detection_id,
          person_type: action === 'confirm' ? personType : undefined,
          person_id: action === 'confirm' ? personId : undefined,
        }))
      if (items.length === 0) {
        Message.info('没有符合条件的候选')
        return
      }
      const result = await confirmFaceScan(action, items)
      Message.success(
        action === 'confirm'
          ? `已确认关联 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`
          : `已驳回 ${result.rejected} 条`,
      )
      await refreshAll()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '审核操作失败'))
    } finally {
      detailActionBusy.value = false
    }
  }

  /** 当前人物明细：勾选批量确认/驳回（结果区 tab 决定目标状态） */
  async function actOnChecked(action: 'confirm' | 'reject') {
    if (detailChecked.value.size === 0) {
      Message.warning('请先勾选要处理的人脸')
      return
    }
    const { personType, personId } = selectedPerson.value
    detailActionBusy.value = true
    try {
      const result = await confirmFaceScan(
        action,
        [...detailChecked.value].map((id) => ({
          detection_id: id,
          person_type: action === 'confirm' ? personType : undefined,
          person_id: action === 'confirm' ? personId : undefined,
        })),
      )
      Message.success(
        action === 'confirm' ? `已确认关联 ${result.confirmed} 条` : `已驳回 ${result.rejected} 条`,
      )
      detailChecked.value.clear()
      await refreshAll()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '审核操作失败'))
    } finally {
      detailActionBusy.value = false
    }
  }

  return {
    detailKey,
    detailItems,
    detailPage,
    detailTotal,
    detailLoading,
    detailChecked,
    detailActionBusy,
    toggleDetail,
    loadDetail,
    actOnPerson,
    actOnChecked,
  }
}
