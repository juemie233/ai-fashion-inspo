/**
 * 人脸明细面板（`useFaceDetailPanel`）的测试。
 *
 * 这是人脸审核页「某个人物展开后」的明细列表 + 批量确认/驳回逻辑，原覆盖率 0%。
 * 它的错误直接影响**生物特征与素材的绑定关系**，且界面只弹一句提示：
 *
 * 1. 结果区 tab 决定目标状态：`pending` 页面操作 = 确认关联，`confirmed` 页面 = 驳回撤销；
 * 2. 「按人物一键审核」要先分页拉全量（每页 200，循环到 total），否则大人物只审了第一页；
 * 3. 「只审高置信度」是前端过滤（`confidence >= 门槛`），过滤后为空要提示而不是发空请求；
 * 4. 确认的 payload 带 `person_type/person_id`，驳回不带（后端按 detection 撤销关联）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  confirmFaceScan: vi.fn(),
  fetchFaceScanResults: vi.fn(),
  refreshAll: vi.fn(async () => {}),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/faceScan', () => ({
  confirmFaceScan: mocks.confirmFaceScan,
  fetchFaceScanResults: mocks.fetchFaceScanResults,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useFaceDetailPanel, type ResultTab } from '../useFaceDetailPanel'

type Person = Parameters<ReturnType<typeof useFaceDetailPanel>['toggleDetail']>[0]

function person(overrides: Record<string, unknown> = {}): Person {
  return { person_type: 'blogger', person_id: 12, ...overrides } as unknown as Person
}

function detection(id: number, confidence = 0.9) {
  return { detection_id: id, confidence } as never
}

function setup(tab: ResultTab = 'pending', highConfidence?: number) {
  const resultTab = ref<ResultTab>(tab)
  const api = useFaceDetailPanel({
    resultTab,
    refreshAll: mocks.refreshAll,
    highConfidence: highConfidence === undefined ? undefined : ref(highConfidence),
  })
  return { api, resultTab }
}

describe('useFaceDetailPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchFaceScanResults.mockResolvedValue({ items: [detection(1)], total: 1 })
    mocks.confirmFaceScan.mockResolvedValue({ confirmed: 1, skipped: 0, rejected: 0 })
  })

  describe('展开/收起与明细加载', () => {
    it('展开：设置 key、重置页码与勾选，按 pending 状态加载明细', async () => {
      const { api } = setup('pending')
      api.detailChecked.value = new Set([99])
      api.detailPage.value = 3

      await api.toggleDetail(person())

      expect(api.detailKey.value).toBe('blogger:12')
      expect(api.detailPage.value).toBe(1)
      expect(api.detailChecked.value.size).toBe(0)
      expect(mocks.fetchFaceScanResults).toHaveBeenCalledWith({
        status: 'pending',
        person_type: 'blogger',
        person_id: 12,
        page: 1,
        size: 50,
      })
      expect(api.detailItems.value).toEqual([detection(1)])
      expect(api.detailTotal.value).toBe(1)
    })

    it('resultTab=confirmed 时按已确认状态加载（操作方向随 tab 变）', async () => {
      const { api } = setup('confirmed')

      await api.toggleDetail(person({ person_type: 'model', person_id: 3 }))

      expect(mocks.fetchFaceScanResults).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'confirmed', person_type: 'model', person_id: 3 }),
      )
    })

    it('再次点击同一人物 = 收起，清空勾选且不再请求', async () => {
      const { api } = setup()
      await api.toggleDetail(person())
      mocks.fetchFaceScanResults.mockClear()

      await api.toggleDetail(person())

      expect(api.detailKey.value).toBe('')
      expect(api.detailChecked.value.size).toBe(0)
      expect(mocks.fetchFaceScanResults).not.toHaveBeenCalled()
    })

    it('切换到另一个人物：重新加载', async () => {
      const { api } = setup()
      await api.toggleDetail(person())

      await api.toggleDetail(person({ person_type: 'model', person_id: 8 }))

      expect(api.detailKey.value).toBe('model:8')
      expect(mocks.fetchFaceScanResults).toHaveBeenCalledTimes(2)
    })

    it('未展开时 loadDetail 直接返回；加载失败提示并复位 loading', async () => {
      const { api } = setup()
      await api.loadDetail() // 未展开
      expect(mocks.fetchFaceScanResults).not.toHaveBeenCalled()

      api.detailKey.value = 'blogger:1'
      mocks.fetchFaceScanResults.mockRejectedValue(null)
      await api.loadDetail()
      expect(mocks.message.error).toHaveBeenCalledWith('加载明细失败')
      expect(api.detailLoading.value).toBe(false)
    })
  })

  describe('按人物一键审核', () => {
    it('分页拉全量（每页 200，直到 total）', async () => {
      mocks.fetchFaceScanResults
        .mockResolvedValueOnce({
          items: Array.from({ length: 200 }, (_, i) => detection(i)),
          total: 250,
        })
        .mockResolvedValueOnce({
          items: Array.from({ length: 50 }, (_, i) => detection(200 + i)),
          total: 250,
        })
      const { api } = setup()

      await api.actOnPerson(person(), 'confirm')

      expect(mocks.fetchFaceScanResults).toHaveBeenCalledTimes(2)
      expect(mocks.fetchFaceScanResults).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2, size: 200 }),
      )
      expect(mocks.confirmFaceScan).toHaveBeenCalledWith(
        'confirm',
        expect.arrayContaining([
          { detection_id: 0, person_type: 'blogger', person_id: 12 },
          { detection_id: 249, person_type: 'blogger', person_id: 12 },
        ]),
      )
    })

    it('只审高置信度：按门槛过滤，全部低于门槛时提示且不发请求', async () => {
      mocks.fetchFaceScanResults.mockResolvedValue({
        items: [detection(1, 0.9), detection(2, 0.5), detection(3, 0.8)],
        total: 3,
      })
      const { api } = setup('pending', 0.85)

      await api.actOnPerson(person(), 'confirm', true)

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('confirm', [
        { detection_id: 1, person_type: 'blogger', person_id: 12 },
      ])

      mocks.confirmFaceScan.mockClear()
      mocks.fetchFaceScanResults.mockResolvedValue({ items: [detection(2, 0.5)], total: 1 })
      await api.actOnPerson(person(), 'confirm', true)
      expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
      expect(mocks.message.info).toHaveBeenCalledWith('没有符合条件的候选')
    })

    it('驳回不带 person_type/person_id（撤销关联由后端按 detection 处理）', async () => {
      mocks.fetchFaceScanResults.mockResolvedValue({ items: [detection(5)], total: 1 })
      mocks.confirmFaceScan.mockResolvedValue({ confirmed: 0, skipped: 0, rejected: 1 })
      const { api } = setup('confirmed')

      await api.actOnPerson(person(), 'reject')

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('reject', [
        { detection_id: 5, person_type: undefined, person_id: undefined },
      ])
      expect(mocks.message.success).toHaveBeenCalledWith('已驳回 1 条')
      expect(mocks.refreshAll).toHaveBeenCalled()
      expect(api.detailActionBusy.value).toBe(false)
    })

    it('确认成功提示含跳过条数', async () => {
      mocks.confirmFaceScan.mockResolvedValue({ confirmed: 3, skipped: 2, rejected: 0 })
      mocks.fetchFaceScanResults.mockResolvedValue({
        items: [detection(1), detection(2), detection(3), detection(4), detection(5)],
        total: 5,
      })
      const { api } = setup()

      await api.actOnPerson(person(), 'confirm')

      expect(mocks.message.success).toHaveBeenCalledWith('已确认关联 3 条（跳过 2 条）')
      expect(mocks.refreshAll).toHaveBeenCalled()
    })

    it('审核失败提示且 busy 复位', async () => {
      mocks.confirmFaceScan.mockRejectedValue(null)
      const { api } = setup()

      await api.actOnPerson(person(), 'confirm')

      expect(mocks.message.error).toHaveBeenCalledWith('审核操作失败')
      expect(api.detailActionBusy.value).toBe(false)
    })
  })

  describe('勾选批量审核', () => {
    it('未勾选时警告且不发请求', async () => {
      const { api } = setup()

      await api.actOnChecked('confirm')

      expect(mocks.message.warning).toHaveBeenCalledWith('请先勾选要处理的人脸')
      expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
    })

    it('确认勾选项：payload 带上当前人物，成功后清空勾选并刷新', async () => {
      const { api } = setup()
      await api.toggleDetail(person({ person_id: 7 }))
      api.detailChecked.value = new Set([21, 22])

      await api.actOnChecked('confirm')

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('confirm', [
        { detection_id: 21, person_type: 'blogger', person_id: 7 },
        { detection_id: 22, person_type: 'blogger', person_id: 7 },
      ])
      expect(mocks.message.success).toHaveBeenCalledWith('已确认关联 1 条')
      expect(api.detailChecked.value.size).toBe(0)
      expect(mocks.refreshAll).toHaveBeenCalled()
      expect(api.detailActionBusy.value).toBe(false)
    })

    it('驳回勾选项：提示驳回条数', async () => {
      const { api } = setup('confirmed')
      await api.toggleDetail(person())
      api.detailChecked.value = new Set([31])
      mocks.confirmFaceScan.mockResolvedValue({ confirmed: 0, skipped: 0, rejected: 1 })

      await api.actOnChecked('reject')

      expect(mocks.confirmFaceScan).toHaveBeenCalledWith('reject', [
        { detection_id: 31, person_type: undefined, person_id: undefined },
      ])
      expect(mocks.message.success).toHaveBeenCalledWith('已驳回 1 条')
    })

    it('勾选审核失败提示且 busy 复位', async () => {
      const { api } = setup()
      await api.toggleDetail(person())
      api.detailChecked.value = new Set([1])
      mocks.confirmFaceScan.mockRejectedValue(null)

      await api.actOnChecked('confirm')

      expect(mocks.message.error).toHaveBeenCalledWith('审核操作失败')
      expect(api.detailActionBusy.value).toBe(false)
    })
  })
})
