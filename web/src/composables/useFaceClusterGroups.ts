/** 人脸聚合分组域：未匹配人脸按疑似同一人聚类的任务、分组列表与整组指派。
 *
 * 为什么单独抽出：人脸页把「扫描/匹配/审核」与「聚合分组」两条链路写在一个
 * 1489 行的视图里，两边的状态、请求与轮询互不相干。这里把聚类这条链路整体
 * 搬出来（状态 + 请求 + 轮询 + 指派），视图只保留编排与模板。
 *
 * 唯一的跨域依赖是「整组指派后要刷新未匹配区」，由调用方以回调注入
 * （与 useAnalysisQueue / useF2Import 等 composable 的既有做法一致）。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  confirmFaceScan,
  fetchFaceClusterDetections,
  fetchFaceClusterGroups,
  fetchFaceClusterTask,
  runFaceCluster,
  type DetectionItem,
  type FaceClusterGroup,
  type FaceClusterGroups,
  type FaceScanTaskOut,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'
import { pollTaskUntilIdle } from '@/utils/taskPollUntilIdle'

export interface UseFaceClusterGroupsOptions {
  /** 整组指派成功后刷新未匹配人脸区（跨域回调） */
  loadUnmatched: () => void | Promise<void>
}

export function useFaceClusterGroups(options: UseFaceClusterGroupsOptions) {
  const clusterTask = ref<FaceScanTaskOut | null>(null)
  const clusterGroups = ref<FaceClusterGroup[]>([])
  const clusterTotal = ref(0)
  const clusterPage = ref(1)
  const clusterSummary = ref<FaceClusterGroups['summary']>(null)
  const clusterLoading = ref(false)
  const clustering = ref(false)
  // 展开的组：group_id → 明细分页状态
  const expandedGroupId = ref<number | null>(null)
  const groupDetailItems = ref<DetectionItem[]>([])
  const groupDetailTotal = ref(0)
  const groupDetailPage = ref(1)
  const groupDetailLoading = ref(false)
  const groupChecked = ref<Set<number>>(new Set())
  const groupActionBusy = ref(false)
  // 整组指派目标（复用未匹配区的人物选择器状态）
  const clusterAssignKind = ref<'blogger' | 'model'>('blogger')
  const clusterAssignPersonId = ref<number | undefined>(undefined)

  /** 拉取最近聚类任务状态 */
  async function loadClusterTask() {
    try {
      const { cluster_task } = await fetchFaceClusterTask()
      clusterTask.value = cluster_task
    } catch {
      /* 静默：聚类未运行过时不报错 */
    }
  }

  /** 开始人脸聚合聚类 */
  async function startCluster() {
    clustering.value = true
    try {
      const { task_id, message } = await runFaceCluster()
      Message.success(message)
      await loadClusterTask()
      void pollClusterUntilIdle(task_id)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '创建聚类任务失败'))
    } finally {
      clustering.value = false
    }
  }

  /** 轮询聚类任务直到终态（2s 间隔；完成后刷新分组） */
  async function pollClusterUntilIdle(taskId: number) {
    await pollTaskUntilIdle({
      taskId,
      intervalMs: 2000,
      refresh: async () => {
        await loadClusterTask()
        return clusterTask.value
      },
      onIdle: loadClusterGroups,
    })
  }

  /** 加载聚合分组（分页） */
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

  /** 展开/收起某分组：加载组内人脸明细 */
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

  /** 整组指派给所选人物（复用 confirm 批量指派链路） */
  async function assignGroup(group: FaceClusterGroup) {
    if (!clusterAssignPersonId.value) {
      Message.warning('请选择要指派的人物')
      return
    }
    const targetIds = group.detection_ids ?? []
    if (targetIds.length === 0) {
      Message.warning('该组没有可指派的人脸')
      return
    }
    groupActionBusy.value = true
    try {
      const result = await confirmFaceScan(
        'confirm',
        targetIds.map((id) => ({
          detection_id: id,
          person_type: clusterAssignKind.value,
          person_id: clusterAssignPersonId.value,
        })),
      )
      Message.success(
        `已整组指派 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
      )
      groupChecked.value.clear()
      await Promise.all([loadClusterGroups(), options.loadUnmatched()])
    } catch (e) {
      Message.error(getApiErrorMessage(e, '整组指派失败'))
    } finally {
      groupActionBusy.value = false
    }
  }

  /** 指派勾选的人脸给所选人物（部分选择后指定博主） */
  async function assignCheckedGroup() {
    if (groupChecked.value.size === 0) {
      Message.warning('请先勾选要指派的人脸')
      return
    }
    if (!clusterAssignPersonId.value) {
      Message.warning('请选择要指派的人物')
      return
    }
    groupActionBusy.value = true
    try {
      const result = await confirmFaceScan(
        'confirm',
        [...groupChecked.value].map((id) => ({
          detection_id: id,
          person_type: clusterAssignKind.value,
          person_id: clusterAssignPersonId.value,
        })),
      )
      Message.success(
        `已指派勾选 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
      )
      groupChecked.value.clear()
      await Promise.all([loadClusterGroups(), options.loadUnmatched()])
    } catch (e) {
      Message.error(getApiErrorMessage(e, '指派勾选失败'))
    } finally {
      groupActionBusy.value = false
    }
  }

  /** 全选当前组全部人脸（跨分页拉全量后勾选；非仅当前页） */
  async function selectAllGroup(group: FaceClusterGroup) {
    const allIds = group.detection_ids ?? []
    if (allIds.length === 0) {
      Message.warning('该组没有可勾选的人脸')
      return
    }
    groupChecked.value = new Set(allIds)
    Message.success(`已全选该组 ${allIds.length} 张人脸`)
  }

  /** 清空当前组勾选 */
  function clearGroupChecked() {
    groupChecked.value = new Set()
  }

  return {
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
    groupActionBusy,
    clusterAssignKind,
    clusterAssignPersonId,
    loadClusterTask,
    startCluster,
    loadClusterGroups,
    toggleGroupDetail,
    loadGroupDetail,
    assignGroup,
    assignCheckedGroup,
    selectAllGroup,
    clearGroupChecked,
  }
}
