/**
 * 人脸聚合分组域（`useFaceClusterGroups`）的测试。
 *
 * 人脸页把「扫描/匹配/审核」与「聚合分组」两条链路写在同一个大视图里，聚类这条
 * 以前 0% 覆盖。它的操作都是**写人脸归属**（整组指派 / 勾选指派），错了会影响
 * 生物特征与素材的绑定关系，且界面只显示一句提示：
 *
 * - 未选人物 / 组内无人脸时必须拦在发请求之前（不能提交空指派）；
 * - 指派成功后要同时刷新「分组列表」与「未匹配人脸区」（两处统计要同步）；
 * - 展开某组才加载明细；收起不请求；切换组要重置分页与勾选；
 * - 全选是「组内全部人脸」，不是仅当前页——跨分页数据来自 `detection_ids`。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  confirmFaceScan: vi.fn(),
  fetchFaceClusterDetections: vi.fn(),
  fetchFaceClusterGroups: vi.fn(),
  fetchFaceClusterTask: vi.fn(),
  runFaceCluster: vi.fn(),
  pollTaskUntilIdle: vi.fn(async () => {}),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  loadUnmatched: vi.fn(),
}))

vi.mock('@/api/faceScan', () => ({
  confirmFaceScan: mocks.confirmFaceScan,
  fetchFaceClusterDetections: mocks.fetchFaceClusterDetections,
  fetchFaceClusterGroups: mocks.fetchFaceClusterGroups,
  fetchFaceClusterTask: mocks.fetchFaceClusterTask,
  runFaceCluster: mocks.runFaceCluster,
}))

vi.mock('@/utils/taskPollUntilIdle', () => ({
  pollTaskUntilIdle: mocks.pollTaskUntilIdle,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useFaceClusterGroups } from '../useFaceClusterGroups'

type Group = Parameters<ReturnType<typeof useFaceClusterGroups>['assignGroup']>[0]

function group(overrides: Record<string, unknown> = {}): Group {
  return {
    group_id: 1,
    detection_ids: [11, 12],
    size: 2,
    ...overrides,
  } as unknown as Group
}

function groupsPage(items: unknown[] = [group()], total = 1, summary: unknown = { groups: 1 }) {
  return { items, total, summary }
}

function setup() {
  return useFaceClusterGroups({ loadUnmatched: mocks.loadUnmatched })
}

describe('useFaceClusterGroups', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.confirmFaceScan.mockResolvedValue({ confirmed: 2, skipped: 0 })
    mocks.fetchFaceClusterDetections.mockResolvedValue({ items: [{ id: 11 }], total: 1 })
    mocks.fetchFaceClusterGroups.mockResolvedValue(groupsPage())
    mocks.fetchFaceClusterTask.mockResolvedValue({ cluster_task: { id: 9, status: 'running' } })
    mocks.runFaceCluster.mockResolvedValue({ task_id: 9, message: '已开始聚类' })
  })

  describe('聚类任务', () => {
    it('loadClusterTask 成功写入最近任务，失败静默（没跑过聚类不是错误）', async () => {
      const c = setup()
      await c.loadClusterTask()
      expect(c.clusterTask.value).toEqual({ id: 9, status: 'running' })

      mocks.fetchFaceClusterTask.mockRejectedValue(new Error('500'))
      await c.loadClusterTask() // 不提示
      expect(mocks.message.error).not.toHaveBeenCalled()
    })

    it('startCluster：提示后端文案、刷新任务状态并启动轮询', async () => {
      const c = setup()
      await c.startCluster()

      expect(mocks.runFaceCluster).toHaveBeenCalled()
      expect(mocks.message.success).toHaveBeenCalledWith('已开始聚类')
      expect(c.clustering.value).toBe(false)
      expect(mocks.pollTaskUntilIdle).toHaveBeenCalledWith(
        expect.objectContaining({ taskId: 9, intervalMs: 2000 }),
      )
    })

    it('startCluster 失败提示且复位 loading', async () => {
      mocks.runFaceCluster.mockRejectedValue(null)
      const c = setup()

      await c.startCluster()

      expect(mocks.message.error).toHaveBeenCalledWith('创建聚类任务失败')
      expect(c.clustering.value).toBe(false)
    })

    it('轮询回调：refresh 拉任务状态，onIdle 刷新分组列表', async () => {
      const c = setup()
      await c.startCluster()

      const opts = mocks.pollTaskUntilIdle.mock.calls.at(-1)![0] as {
        refresh: () => Promise<unknown>
        onIdle: () => unknown
      }
      await opts.refresh()
      expect(mocks.fetchFaceClusterTask).toHaveBeenCalled()

      opts.onIdle()
      await Promise.resolve()
      expect(mocks.fetchFaceClusterGroups).toHaveBeenCalled()
    })
  })

  describe('分组列表', () => {
    it('loadClusterGroups 写入 items/total/summary，loading 过程态正确', async () => {
      const c = setup()
      const promise = c.loadClusterGroups()
      expect(c.clusterLoading.value).toBe(true)
      await promise

      expect(mocks.fetchFaceClusterGroups).toHaveBeenCalledWith({ page: 1, size: 20 })
      expect(c.clusterGroups.value).toHaveLength(1)
      expect(c.clusterTotal.value).toBe(1)
      expect(c.clusterSummary.value).toEqual({ groups: 1 })
      expect(c.clusterLoading.value).toBe(false)
    })

    it('加载失败提示兜底文案并复位 loading', async () => {
      mocks.fetchFaceClusterGroups.mockRejectedValue(null)
      const c = setup()

      await c.loadClusterGroups()

      expect(mocks.message.error).toHaveBeenCalledWith('加载聚合分组失败')
      expect(c.clusterLoading.value).toBe(false)
    })
  })

  describe('组内明细', () => {
    it('展开组：设置展开 id、清空勾选、重置分页并加载明细', async () => {
      const c = setup()
      c.groupChecked.value = new Set([99])
      c.groupDetailPage.value = 3

      await c.toggleGroupDetail(group({ group_id: 5 }))

      expect(c.expandedGroupId.value).toBe(5)
      expect(c.groupChecked.value.size).toBe(0)
      expect(c.groupDetailPage.value).toBe(1)
      expect(mocks.fetchFaceClusterDetections).toHaveBeenCalledWith(5, { page: 1, size: 50 })
      expect(c.groupDetailItems.value).toEqual([{ id: 11 }])
      expect(c.groupDetailTotal.value).toBe(1)
    })

    it('再次点击同组 = 收起：清 id 与勾选，不再请求明细', async () => {
      const c = setup()
      await c.toggleGroupDetail(group({ group_id: 5 }))
      mocks.fetchFaceClusterDetections.mockClear()

      await c.toggleGroupDetail(group({ group_id: 5 }))

      expect(c.expandedGroupId.value).toBeNull()
      expect(c.groupChecked.value.size).toBe(0)
      expect(mocks.fetchFaceClusterDetections).not.toHaveBeenCalled()
    })

    it('loadGroupDetail 在未展开时直接返回；失败提示', async () => {
      const c = setup()
      await c.loadGroupDetail() // 未展开
      expect(mocks.fetchFaceClusterDetections).not.toHaveBeenCalled()

      c.expandedGroupId.value = 7
      mocks.fetchFaceClusterDetections.mockRejectedValue(null)
      await c.loadGroupDetail()
      expect(mocks.message.error).toHaveBeenCalledWith('加载组内人脸失败')
      expect(c.groupDetailLoading.value).toBe(false)
    })
  })

  describe('整组指派', () => {
    it('未选人物 → 警告且不发请求', async () => {
      const c = setup()
      await c.assignGroup(group())

      expect(mocks.message.warning).toHaveBeenCalledWith('请选择要指派的人物')
      expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
    })

    it('组内无人脸 → 警告且不发请求', async () => {
      const c = setup()
      c.clusterAssignPersonId.value = 3

      await c.assignGroup(group({ detection_ids: [] }))

      expect(mocks.message.warning).toHaveBeenCalledWith('该组没有可指派的人脸')
      expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
    })

    it('指派成功：payload 带人物类型/ID、清空勾选、同时刷新分组与未匹配区', async () => {
      const c = setup()
      c.clusterAssignPersonId.value = 3
      c.clusterAssignKind.value = 'model'
      c.groupChecked.value = new Set([11])

      await c.assignGroup(group({ detection_ids: [11, 12] }))

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('confirm', [
        { detection_id: 11, person_type: 'model', person_id: 3 },
        { detection_id: 12, person_type: 'model', person_id: 3 },
      ])
      expect(mocks.message.success).toHaveBeenCalledWith('已整组指派 2 条')
      expect(c.groupChecked.value.size).toBe(0)
      expect(mocks.fetchFaceClusterGroups).toHaveBeenCalled()
      expect(mocks.loadUnmatched).toHaveBeenCalled()
      expect(c.groupActionBusy.value).toBe(false)
    })

    it('指派提示带跳过条数；失败提示且复位 busy', async () => {
      const c = setup()
      c.clusterAssignPersonId.value = 3
      mocks.confirmFaceScan.mockResolvedValue({ confirmed: 1, skipped: 1 })
      await c.assignGroup(group())
      expect(mocks.message.success).toHaveBeenCalledWith('已整组指派 1 条（跳过 1 条）')

      mocks.confirmFaceScan.mockRejectedValue(null)
      await c.assignGroup(group())
      expect(mocks.message.error).toHaveBeenCalledWith('整组指派失败')
      expect(c.groupActionBusy.value).toBe(false)
    })
  })

  describe('勾选指派', () => {
    it('未勾选 / 未选人物都拦在请求前', async () => {
      const c = setup()
      await c.assignCheckedGroup()
      expect(mocks.message.warning).toHaveBeenCalledWith('请先勾选要指派的人脸')

      c.groupChecked.value = new Set([11])
      await c.assignCheckedGroup()
      expect(mocks.message.warning).toHaveBeenCalledWith('请选择要指派的人物')
      expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
    })

    it('指派勾选成功：payload 来自选中集合并刷新两处', async () => {
      const c = setup()
      c.groupChecked.value = new Set([11, 13])
      c.clusterAssignPersonId.value = 8
      mocks.confirmFaceScan.mockResolvedValue({ confirmed: 2, skipped: 0 })

      await c.assignCheckedGroup()

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('confirm', [
        { detection_id: 11, person_type: 'blogger', person_id: 8 },
        { detection_id: 13, person_type: 'blogger', person_id: 8 },
      ])
      expect(mocks.message.success).toHaveBeenCalledWith('已指派勾选 2 条')
      expect(c.groupChecked.value.size).toBe(0)
      expect(mocks.loadUnmatched).toHaveBeenCalled()
    })

    it('勾选指派失败提示「指派勾选失败」', async () => {
      const c = setup()
      c.groupChecked.value = new Set([11])
      c.clusterAssignPersonId.value = 8
      mocks.confirmFaceScan.mockRejectedValue(null)

      await c.assignCheckedGroup()

      expect(mocks.message.error).toHaveBeenCalledWith('指派勾选失败')
      expect(c.groupActionBusy.value).toBe(false)
    })
  })

  describe('全选与清空', () => {
    it('全选使用组内 detection_ids（跨分页），不是当前页', async () => {
      const c = setup()

      await c.selectAllGroup(group({ detection_ids: [1, 2, 3, 4, 5] }))

      expect([...c.groupChecked.value]).toEqual([1, 2, 3, 4, 5])
      expect(mocks.message.success).toHaveBeenCalledWith('已全选该组 5 张人脸')
    })

    it('空组全选给警告', async () => {
      const c = setup()
      await c.selectAllGroup(group({ detection_ids: [] }))
      expect(mocks.message.warning).toHaveBeenCalledWith('该组没有可勾选的人脸')
    })

    it('clearGroupChecked 清空勾选', () => {
      const c = setup()
      c.groupChecked.value = new Set([1, 2])
      c.clearGroupChecked()
      expect(c.groupChecked.value.size).toBe(0)
    })
  })
})
