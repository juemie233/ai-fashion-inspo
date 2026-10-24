/** usePersonBatch 测试：多选 → 批量纳入追踪/删除/清头像、同名合并、覆盖率加载。
 *
 * 关注四件事：① 破坏性操作的结果要**如实汇报**（删掉几个、几个被挡下及原因）；
 * ② 动作完成后清空选择并刷新列表（避免误触第二次、列表及时更新）；
 * ③ 同名合并以素材数最多者为组主，其余逐个并入；
 * ④ 覆盖率只是展示，接口失败不能影响页面。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

import { usePersonBatch } from '../usePersonBatch'

const mocks = vi.hoisted(() => ({
  batchPromote: vi.fn(),
  batchDelete: vi.fn(),
  clearAvatars: vi.fn(),
  avatarCoverage: vi.fn(),
  duplicateNames: vi.fn(),
  linkGroup: vi.fn(),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    batchPromote: mocks.batchPromote,
    batchDelete: mocks.batchDelete,
    clearAvatars: mocks.clearAvatars,
    avatarCoverage: mocks.avatarCoverage,
    duplicateNames: mocks.duplicateNames,
    linkGroup: mocks.linkGroup,
  },
}))

function groupOf(items: Array<{ id: number; name: string; count: number }>) {
  return {
    key: '折乙',
    items: items.map((item) => ({
      id: item.id,
      name: item.name,
      platform: 'douyin',
      source: 'manual',
      person_group_id: null,
      profile_url: null,
      inspiration_count: item.count,
    })),
  }
}

describe('usePersonBatch', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((fn) => fn.mockReset())
    vi.restoreAllMocks()
  })

  it('多选与清空选择', () => {
    const { selectedKeys, selectedCount, onSelectionChange, clearSelection } = usePersonBatch(
      'blogger',
      vi.fn(),
    )

    onSelectionChange([1, 2, 3])
    expect(selectedKeys.value).toEqual([1, 2, 3])
    expect(selectedCount.value).toBe(3)

    clearSelection()
    expect(selectedCount.value).toBe(0)
  })

  it('批量纳入追踪：汇报成功数与被跳过的原因，结束后清空并刷新', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const afterMutate = vi.fn()
    mocks.batchPromote.mockResolvedValue({
      promoted: 3,
      skipped: [{ id: 9, name: '已登记博', reason: '不是「自动登记」的博主，无需纳入' }],
    })
    const batch = usePersonBatch('blogger', afterMutate)
    batch.onSelectionChange([1, 2, 3, 9])

    await batch.batchPromote()

    expect(mocks.batchPromote).toHaveBeenCalledWith([1, 2, 3, 9])
    expect(success).toHaveBeenCalledWith(expect.stringContaining('已纳入追踪 3 位'))
    expect(success).toHaveBeenCalledWith(expect.stringContaining('1 位未处理'))
    expect(afterMutate).toHaveBeenCalledTimes(1)
    expect(batch.selectedCount.value).toBe(0)
  })

  it('批量删除：把「有素材所以没删掉」的原因说清楚，不谎报全部成功', async () => {
    const warning = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.batchDelete.mockResolvedValue({
      deleted: 2,
      blocked: [{ id: 7, name: '有素材博', reason: '该博主下仍有 12 个素材关联，无法删除' }],
    })
    mocks.avatarCoverage.mockResolvedValue({ total: 10, with_avatar: 1, fillable: 8 })
    const batch = usePersonBatch('blogger', vi.fn())
    batch.onSelectionChange([1, 2, 7])

    await batch.batchDelete()

    expect(mocks.batchDelete).toHaveBeenCalledWith([1, 2, 7])
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('已删除 2 位'))
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('1 位未删除'))
    expect(batch.coverage.value).toEqual({ total: 10, with_avatar: 1, fillable: 8 })
  })

  it('清除所选头像：全部没头像时给出提示而不是假成功', async () => {
    const info = vi.spyOn(Message, 'info').mockImplementation((() => {}) as never)
    mocks.clearAvatars.mockResolvedValue({ cleared: 0 })
    mocks.avatarCoverage.mockResolvedValue({ total: 1, with_avatar: 0, fillable: 1 })
    const batch = usePersonBatch('blogger', vi.fn())
    batch.onSelectionChange([5])

    await batch.clearSelectedAvatars()

    expect(mocks.clearAvatars).toHaveBeenCalledWith([5])
    expect(info).toHaveBeenCalledWith('所选博主都没有头像')
  })

  it('覆盖率接口失败：静默（卡片不显示），不影响其它动作', async () => {
    mocks.avatarCoverage.mockRejectedValue(new Error('boom'))
    const batch = usePersonBatch('blogger', vi.fn())

    await batch.loadCoverage()

    expect(batch.coverage.value).toBeNull()
  })

  it('模特没有头像/同名能力：不请求覆盖率', async () => {
    const batch = usePersonBatch('model', vi.fn())
    await batch.loadCoverage()
    expect(mocks.avatarCoverage).not.toHaveBeenCalled()
  })

  it('同名合并：以素材最多者为组主，其余逐个并入，成功后从列表移除该组', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const afterMutate = vi.fn()
    mocks.linkGroup.mockResolvedValue({})
    const batch = usePersonBatch('blogger', afterMutate)
    batch.duplicateGroups.value = [
      groupOf([
        { id: 2, name: '折乙', count: 12 },
        { id: 5, name: '折乙', count: 0 },
      ]),
    ]

    const ok = await batch.mergeGroup(batch.duplicateGroups.value[0])

    expect(ok).toBe(true)
    expect(mocks.linkGroup).toHaveBeenCalledTimes(1)
    expect(mocks.linkGroup).toHaveBeenCalledWith({ blogger_id: 5, target_blogger_id: 2 })
    expect(success).toHaveBeenCalledWith(expect.stringContaining('已合并'))
    expect(batch.duplicateGroups.value).toHaveLength(0)
    expect(afterMutate).toHaveBeenCalledTimes(1)
  })

  it('删除空壳账号：只删 0 素材的，删完把剩不足 2 个的组去掉', async () => {
    mocks.batchDelete.mockResolvedValue({ deleted: 1, blocked: [] })
    mocks.avatarCoverage.mockResolvedValue({ total: 1, with_avatar: 0, fillable: 0 })
    const batch = usePersonBatch('blogger', vi.fn())
    batch.duplicateGroups.value = [
      groupOf([
        { id: 2, name: '折乙', count: 12 },
        { id: 5, name: '折乙', count: 0 },
      ]),
    ]

    await batch.removeEmptyAccounts(batch.duplicateGroups.value[0])

    expect(mocks.batchDelete).toHaveBeenCalledWith([5])
    expect(batch.duplicateGroups.value).toHaveLength(0)
  })
})
