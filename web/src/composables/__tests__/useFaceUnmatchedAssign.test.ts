/**
 * 未匹配人脸指派（`useFaceUnmatchedAssign`）的测试。
 *
 * 这里的关键风险是「指派了但没真派」：
 * - 勾选为空 / 没选人物时必须**不发请求**、只提示，否则后端会收到一条空指派；
 * - 提交的 payload 必须是 `{ detection_id, person_type, person_id }` 三元组，
 *   person_type 跟着当前选中的博主/模特走；
 * - 成功后要清空勾选并触发整页刷新，否则界面还挂着已被指派的人脸。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchFaceScanResults: vi.fn(),
  confirmFaceScan: vi.fn(),
  bloggerFetchList: vi.fn(),
  modelFetchList: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/api/faceScan', () => ({
  fetchFaceScanResults: mocks.fetchFaceScanResults,
  confirmFaceScan: mocks.confirmFaceScan,
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: mocks.bloggerFetchList },
  modelsApi: { fetchList: mocks.modelFetchList },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return {
    ...actual,
    Message: { success: mocks.success, warning: mocks.warning, error: mocks.error },
  }
})

import { useFaceUnmatchedAssign } from '../useFaceUnmatchedAssign'

function makeApi(over: { refreshAll?: () => Promise<void> } = {}) {
  const refreshAll = vi.fn(over.refreshAll ?? (async () => {}))
  const api = useFaceUnmatchedAssign({ refreshAll })
  return { api, refreshAll }
}

describe('useFaceUnmatchedAssign', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchFaceScanResults.mockResolvedValue({ items: [], total: 0 })
    mocks.confirmFaceScan.mockResolvedValue({ confirmed: 2, skipped: 0 })
    mocks.bloggerFetchList.mockResolvedValue({ items: [], total: 0 })
    mocks.modelFetchList.mockResolvedValue({ items: [], total: 0 })
  })

  it('加载未匹配人脸：按 unmatched=true + 当前页请求，回填列表与总数', async () => {
    mocks.fetchFaceScanResults.mockResolvedValue({
      items: [{ detection_id: 1 }, { detection_id: 2 }],
      total: 7,
    })
    const { api } = makeApi()

    api.unmatchedPage.value = 2
    await api.loadUnmatched()

    expect(mocks.fetchFaceScanResults).toHaveBeenCalledWith({
      status: 'pending',
      unmatched: true,
      page: 2,
      size: 50,
    })
    expect(api.unmatchedItems.value).toHaveLength(2)
    expect(api.unmatchedTotal.value).toBe(7)
    expect(api.unmatchedLoading.value).toBe(false)
  })

  it('加载失败：提示错误且不留下半截数据', async () => {
    mocks.fetchFaceScanResults.mockRejectedValue(new Error('boom'))
    const { api } = makeApi()

    await api.loadUnmatched()

    expect(mocks.error).toHaveBeenCalled()
    expect(api.unmatchedLoading.value).toBe(false)
  })

  it('拉人物候选：博主档位用 bloggersApi，模特档位用 modelsApi，分页拉全', async () => {
    mocks.modelFetchList
      .mockResolvedValueOnce({ items: [{ id: 1, name: '小美' }], total: 2 })
      .mockResolvedValueOnce({ items: [{ id: 2, name: '阿岚' }], total: 2 })
    const { api } = makeApi()

    api.assignKind.value = 'model'
    await api.loadAssignOptions()

    expect(mocks.modelFetchList).toHaveBeenCalledTimes(2)
    expect(mocks.modelFetchList).toHaveBeenNthCalledWith(1, { page: 1, size: 200, sort: 'name' })
    expect(api.assignOptions.value).toEqual([
      { label: '小美', value: 1 },
      { label: '阿岚', value: 2 },
    ])
    expect(api.assignPersonId.value).toBeUndefined()
  })

  it('没勾选就指派：只提示，不发请求', async () => {
    const { api, refreshAll } = makeApi()

    api.assignPersonId.value = 9
    await api.assignUnmatched()

    expect(mocks.warning).toHaveBeenCalledWith('请先勾选要指派的人脸')
    expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
    expect(refreshAll).not.toHaveBeenCalled()
  })

  it('没选人物就指派：只提示，不发请求', async () => {
    const { api } = makeApi()

    api.unmatchedChecked.value = new Set([1, 2])
    await api.assignUnmatched()

    expect(mocks.warning).toHaveBeenCalledWith('请选择要指派的人物')
    expect(mocks.confirmFaceScan).not.toHaveBeenCalled()
  })

  it('指派成功：payload 用当前人物类型，清空勾选并整页刷新', async () => {
    mocks.confirmFaceScan.mockResolvedValue({ confirmed: 2, skipped: 1 })
    const { api, refreshAll } = makeApi()

    api.assignKind.value = 'model'
    api.unmatchedChecked.value = new Set([11, 12])
    api.assignPersonId.value = 33
    await api.assignUnmatched()

    expect(mocks.confirmFaceScan).toHaveBeenCalledWith('confirm', [
      { detection_id: 11, person_type: 'model', person_id: 33 },
      { detection_id: 12, person_type: 'model', person_id: 33 },
    ])
    expect(mocks.success).toHaveBeenCalledWith('已指派 2 条（跳过 1 条）')
    expect(api.unmatchedChecked.value.size).toBe(0)
    expect(refreshAll).toHaveBeenCalled()
    expect(api.assigning.value).toBe(false)
  })

  it('指派失败：提示错误，勾选保留（便于重试）', async () => {
    mocks.confirmFaceScan.mockRejectedValue(new Error('boom'))
    const { api, refreshAll } = makeApi()

    api.unmatchedChecked.value = new Set([11])
    api.assignPersonId.value = 33
    await api.assignUnmatched()

    expect(mocks.error).toHaveBeenCalled()
    expect(api.unmatchedChecked.value.size).toBe(1)
    expect(refreshAll).not.toHaveBeenCalled()
    expect(api.assigning.value).toBe(false)
  })
})
