/**
 * 标签合并（`useTagMerge`）的测试。
 *
 * 合并是**不可逆写操作**（source 标签被删除、其素材关联迁移到 target），
 * 因此这里钉住四件事：
 * - 无别名时只调 merge；有别名时 merge 完再 createAlias（顺序不能反）；
 * - 成功后广播 merged 事件（各页据此刷新标签列表）；
 * - 失败要透出后端 detail 并返回 ok=false；
 * - 合并成功但建别名失败时，目前返回 ok=false 且**不广播**事件——这是
 *   已知的部分失败语义（source 已删除但界面显示失败），用例锁定现状并在报告中提示。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  mergeTags: vi.fn(),
  createAlias: vi.fn(),
  notifyTagChanged: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/tags', () => ({
  mergeTags: mocks.mergeTags,
  createAlias: mocks.createAlias,
}))

vi.mock('@/composables/useTagEvents', () => ({
  useTagEvents: () => ({ notifyTagChanged: mocks.notifyTagChanged }),
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useTagMerge } from '../useTagMerge'

describe('useTagMerge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.mergeTags.mockResolvedValue({ updated: 3 })
    mocks.createAlias.mockResolvedValue({ id: 1 })
  })

  it('不带别名：只调 merge、提示「已合并」并广播 merged 事件', async () => {
    const { merge } = useTagMerge()

    await expect(merge(1, 2)).resolves.toEqual({ ok: true })

    expect(mocks.mergeTags).toHaveBeenCalledWith(1, 2)
    expect(mocks.createAlias).not.toHaveBeenCalled()
    expect(mocks.message.success).toHaveBeenCalledWith('已合并')
    expect(mocks.notifyTagChanged).toHaveBeenCalledWith({
      type: 'merged',
      tagIds: [1],
      targetId: 2,
    })
  })

  it('带别名：先 merge 再 createAlias，提示带别名文案', async () => {
    const { merge } = useTagMerge()

    await expect(merge(3, 4, '法式')).resolves.toEqual({ ok: true })

    expect(mocks.mergeTags).toHaveBeenCalledWith(3, 4)
    expect(mocks.createAlias).toHaveBeenCalledWith(4, '法式')
    expect(mocks.message.success).toHaveBeenCalledWith('已合并并将「法式」设为别名')
    expect(mocks.notifyTagChanged).toHaveBeenCalledWith({
      type: 'merged',
      tagIds: [3],
      targetId: 4,
    })
  })

  it('合并失败：透出后端 detail、返回 ok=false、不广播不建别名', async () => {
    mocks.mergeTags.mockRejectedValue({ response: { data: { detail: '目标标签不存在' } } })
    const { merge } = useTagMerge()

    await expect(merge(1, 999, '旧名')).resolves.toEqual({ ok: false })

    expect(mocks.message.error).toHaveBeenCalledWith('目标标签不存在')
    expect(mocks.createAlias).not.toHaveBeenCalled()
    expect(mocks.notifyTagChanged).not.toHaveBeenCalled()
  })

  it('失败且无后端文案：回退「合并失败」', async () => {
    mocks.mergeTags.mockRejectedValue({})
    const { merge } = useTagMerge()

    await merge(1, 2)

    expect(mocks.message.error).toHaveBeenCalledWith('合并失败')
  })

  it('合并成功但建别名失败：当前实现返回 ok=false 且不广播（部分失败语义）', async () => {
    mocks.createAlias.mockRejectedValue({ response: { data: { detail: '别名已存在' } } })
    const { merge } = useTagMerge()

    await expect(merge(1, 2, '法式')).resolves.toEqual({ ok: false })

    expect(mocks.mergeTags).toHaveBeenCalledTimes(1)
    expect(mocks.message.error).toHaveBeenCalledWith('别名已存在')
    // 已知现状：merge 已落库，但这里不会 notify，调用方若只依赖事件刷新会看到旧列表
    expect(mocks.notifyTagChanged).not.toHaveBeenCalled()
  })
})
