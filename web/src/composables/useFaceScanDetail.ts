/** 人脸扫描明细展开：按人物展开查看人脸明细。 */

import { Message } from '@arco-design/web-vue'
import { computed, ref, type Ref } from 'vue'
import { fetchFaceScanResults, type DetectionItem, type PersonAggregateItem } from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'

export function useFaceScanDetail(resultTab: Ref<string>) {
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
        status: resultTab.value === 'confirmed' ? 'confirmed' : 'pending',
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
    status: 'pending' | 'confirmed',
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

  /** 全选/取消全选当前明细页 */
  function toggleSelectAllDetail() {
    if (detailItems.value.length === 0) return
    const next = new Set(detailChecked.value)
    const allSelected = detailItems.value.every((i) => next.has(i.detection_id))
    if (allSelected) {
      detailItems.value.forEach((i) => next.delete(i.detection_id))
    } else {
      detailItems.value.forEach((i) => next.add(i.detection_id))
    }
    detailChecked.value = next
  }

  /** 重新加载明细（如果展开） */
  async function reloadDetailIfOpen() {
    if (detailKey.value) {
      detailPage.value = 1
      await loadDetail()
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
    selectedPerson,
    toggleDetail,
    loadDetail,
    fetchAllDetections,
    toggleSelectAllDetail,
    reloadDetailIfOpen,
  }
}
