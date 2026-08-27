/** 人脸扫描结果加载：聚合列表、未匹配列表、聚类分组的加载。 */

import { Message } from '@arco-design/web-vue'
import { ref } from 'vue'
import {
  fetchFaceClusterDetections,
  fetchFaceClusterGroups,
  fetchFaceClusterTask,
  fetchFaceScanResults,
  runFaceCluster,
  type DetectionItem,
  type FaceClusterGroup,
  type FaceClusterGroups,
  type FaceScanTaskOut,
  type PersonAggregateItem,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'

/** 聚合列表状态 */
export interface AggregatesState {
  pendingPersons: PersonAggregateItem[]
  pendingPage: number
  pendingTotal: number
  confirmedPersons: PersonAggregateItem[]
  confirmedPage: number
  confirmedTotal: number
  loading: boolean
}

/** 未匹配状态 */
export interface UnmatchedState {
  items: DetectionItem[]
  page: number
  total: number
  loading: boolean
  checked: Set<number>
  assignKind: 'blogger' | 'model'
  assignPersonId: number | undefined
  assignOptions: Array<{ label: string; value: number }>
  assignLoading: boolean
  assigning: boolean
}

/** 聚类分组状态 */
export interface ClusterState {
  task: FaceScanTaskOut | null
  groups: FaceClusterGroup[]
  total: number
  page: number
  summary: FaceClusterGroups['summary'] | null
  loading: boolean
  clustering: boolean
  expandedGroupId: number | null
  groupDetailItems: DetectionItem[]
  groupDetailTotal: number
  groupDetailPage: number
  groupDetailLoading: boolean
  checked: Set<number>
  assignKind: 'blogger' | 'model'
  assignPersonId: number | undefined
  actionBusy: boolean
}

export function useFaceScanResults() {
  // ── 聚合列表 ──
  const pendingPersons = ref<PersonAggregateItem[]>([])
  const pendingPage = ref(1)
  const pendingTotal = ref(0)
  const confirmedPersons = ref<PersonAggregateItem[]>([])
  const confirmedPage = ref(1)
  const confirmedTotal = ref(0)
  const personsLoading = ref(false)

  /** 加载聚合列表（待审核 + 已确认） */
  async function loadAggregates() {
    personsLoading.value = true
    try {
      const [pending, confirmed] = await Promise.all([
        fetchFaceScanResults({ status: 'pending', page: pendingPage.value, size: 50 }),
        fetchFaceScanResults({ status: 'confirmed', page: confirmedPage.value, size: 50 }),
      ])
      pendingPersons.value = pending.items as PersonAggregateItem[]
      pendingTotal.value = pending.total
      confirmedPersons.value = confirmed.items as PersonAggregateItem[]
      confirmedTotal.value = confirmed.total
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载结果失败'))
    } finally {
      personsLoading.value = false
    }
  }

  // ── 未匹配 ──
  const unmatchedItems = ref<DetectionItem[]>([])
  const unmatchedPage = ref(1)
  const unmatchedTotal = ref(0)
  const unmatchedLoading = ref(false)
  const unmatchedChecked = ref<Set<number>>(new Set())
  const assignKind = ref<'blogger' | 'model'>('blogger')
  const assignPersonId = ref<number | undefined>(undefined)
  const assignOptions = ref<Array<{ label: string; value: number }>>([])
  const assignLoading = ref(false)
  const assigning = ref(false)

  /** 加载未匹配列表 */
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

  /** 拉取人物选择候选（博主/模特全量） */
  async function loadAssignOptions() {
    assignLoading.value = true
    try {
      const { bloggersApi, modelsApi } = await import('@/api/persons')
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

  // ── 聚类分组 ──
  const clusterTask = ref<FaceScanTaskOut | null>(null)
  const clusterGroups = ref<FaceClusterGroup[]>([])
  const clusterTotal = ref(0)
  const clusterPage = ref(1)
  const clusterSummary = ref<FaceClusterGroups['summary']>(null)
  const clusterLoading = ref(false)
  const clustering = ref(false)
  const expandedGroupId = ref<number | null>(null)
  const groupDetailItems = ref<DetectionItem[]>([])
  const groupDetailTotal = ref(0)
  const groupDetailPage = ref(1)
  const groupDetailLoading = ref(false)
  const groupChecked = ref<Set<number>>(new Set())
  const clusterAssignKind = ref<'blogger' | 'model'>('blogger')
  const clusterAssignPersonId = ref<number | undefined>(undefined)
  const groupActionBusy = ref(false)

  /** 拉取聚类任务状态 */
  async function loadClusterTask() {
    try {
      const { cluster_task } = await fetchFaceClusterTask()
      clusterTask.value = cluster_task
    } catch {
      /* 静默：聚类未运行过时不报错 */
    }
  }

  /** 开始人脸聚合聚类 */
  async function startCluster(onRefresh?: () => Promise<void>) {
    clustering.value = true
    try {
      const { task_id, message } = await runFaceCluster()
      Message.success(message)
      await loadClusterTask()
      void pollClusterUntilIdle(task_id, onRefresh)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建聚类任务失败'))
    } finally {
      clustering.value = false
    }
  }

  /** 轮询聚类任务直到终态 */
  async function pollClusterUntilIdle(taskId: number, onRefresh?: () => Promise<void>) {
    while (true) {
      await new Promise((r) => setTimeout(r, 2000))
      await loadClusterTask()
      const current = clusterTask.value
      if (!current || current.id !== taskId || !['running', 'pending'].includes(current.status)) {
        await loadClusterGroups()
        return
      }
    }
  }

  /** 加载聚合分组 */
  async function loadClusterGroups() {
    clusterLoading.value = true
    try {
      const data = await fetchFaceClusterGroups({ page: clusterPage.value, size: 20 })
      clusterGroups.value = data.items
      clusterTotal.value = data.total
      clusterSummary.value = data.summary
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载聚合分组失败'))
    } finally {
      clusterLoading.value = false
    }
  }

  /** 展开分组明细 */
  async function toggleGroupDetail(group: FaceClusterGroup) {
    if (expandedGroupId.value === group.group_id) {
      expandedGroupId.value = null
      groupChecked.value = new Set()
      return
    }
    expandedGroupId.value = group.group_id
    groupChecked.value = new Set()
    groupDetailPage.value = 1
    await loadGroupDetail()
  }

  async function loadGroupDetail() {
    if (expandedGroupId.value === null) return
    groupDetailLoading.value = true
    try {
      const data = await fetchFaceClusterDetections(expandedGroupId.value, {
        page: groupDetailPage.value,
        size: 50,
      })
      groupDetailItems.value = data.items
      groupDetailTotal.value = data.total
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载组内人脸失败'))
    } finally {
      groupDetailLoading.value = false
    }
  }

  /** 全选当前组 */
  async function selectAllGroup(group: FaceClusterGroup) {
    const allIds = group.detection_ids ?? []
    if (allIds.length === 0) {
      Message.warning('该组没有可勾选的人脸')
      return
    }
    groupChecked.value = new Set(allIds)
    Message.success(`已全选该组 ${allIds.length} 张人脸`)
  }

  function clearGroupChecked() {
    groupChecked.value = new Set()
  }

  return {
    // 聚合列表
    pendingPersons,
    pendingPage,
    pendingTotal,
    confirmedPersons,
    confirmedPage,
    confirmedTotal,
    personsLoading,
    loadAggregates,
    // 未匹配
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
    // 聚类
    clusterTask,
    clusterGroups,
    clusterTotal,
    clusterPage,
    clusterSummary,
    clusterLoading,
    clustering,
    expandedGroupId,
    groupDetailItems,
    groupDetailTotal,
    groupDetailPage,
    groupDetailLoading,
    groupChecked,
    clusterAssignKind,
    clusterAssignPersonId,
    groupActionBusy,
    loadClusterTask,
    startCluster,
    loadClusterGroups,
    toggleGroupDetail,
    loadGroupDetail,
    selectAllGroup,
    clearGroupChecked,
  }
}
