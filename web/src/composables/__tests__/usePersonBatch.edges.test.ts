/**
 * 人物批量操作（usePersonBatch）**补齐用例**：既有 usePersonBatch.test.ts 覆盖了主成功路径，
 * 本文件补「空选择 / 失败 / 无结果 / 未覆盖的提示分支」：
 *
 * - 未勾选任何行时所有批量动作都应直接返回（不置 busy、不发请求、不清选择）；
 * - 计入追踪 0 人时用 warning（含第一条跳过原因），不是成功提示；
 * - 批量删除无被挡下项时给纯成功文案；失败时保持选择、busy 复位；
 * - 清除头像成功/失败两条反馈；
 * - 同名建议弹窗的加载成功、加载失败（清空列表）与加载态；
 * - 同名合并不足 2 人直接拒绝；合并/删空壳失败时提示且不改动分组。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  batchPromote: vi.fn(),
  batchDelete: vi.fn(),
  clearAvatars: vi.fn(),
  avatarCoverage: vi.fn(),
  duplicateNames: vi.fn(),
  linkGroup: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
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

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { usePersonBatch } from '../usePersonBatch'

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

function setup(afterMutate = vi.fn()) {
  const api = usePersonBatch('blogger', afterMutate)
  return { api, afterMutate }
}

beforeEach(() => {
  Object.values(mocks).forEach((fn) => {
    if (typeof fn === 'function') fn.mockReset()
  })
  Object.values(mocks.message).forEach((fn) => fn.mockReset())
  mocks.avatarCoverage.mockResolvedValue({ total: 1, with_avatar: 0, missing: 1 })
})

describe('空选择：批量动作直接返回', () => {
  it('未勾选时计入追踪/删除/清头像都不发请求、不置 busy、不清选择', async () => {
    const { api, afterMutate } = setup()

    await api.batchPromote()
    await api.batchDelete()
    await api.clearSelectedAvatars()

    expect(mocks.batchPromote).not.toHaveBeenCalled()
    expect(mocks.batchDelete).not.toHaveBeenCalled()
    expect(mocks.clearAvatars).not.toHaveBeenCalled()
    expect(afterMutate).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
    expect(api.selectedKeys.value).toEqual([])
  })
})

describe('批量纳入追踪的补充分支', () => {
  it('promoted=0：warning 提示「没有可纳入追踪的博主」并附第一条跳过原因', async () => {
    mocks.batchPromote.mockResolvedValue({
      promoted: 0,
      skipped: [{ id: 3, name: '博主三', reason: '已是手动来源' }],
    })
    const { api, afterMutate } = setup()
    api.onSelectionChange([3])

    await api.batchPromote()

    expect(mocks.message.warning).toHaveBeenCalledWith(
      '没有可纳入追踪的博主（博主三：已是手动来源）',
    )
    expect(mocks.message.success).not.toHaveBeenCalled()
    // 无论成败都清空选择并刷新
    expect(api.selectedKeys.value).toEqual([])
    expect(afterMutate).toHaveBeenCalledTimes(1)
    expect(api.busy.value).toBe(false)
  })

  it('请求失败：提示后端 detail、保留选择、busy 复位', async () => {
    mocks.batchPromote.mockRejectedValue({
      response: { data: { detail: '数据库锁了' } },
    })
    const { api, afterMutate } = setup()
    api.onSelectionChange([1, 2])

    await api.batchPromote()

    expect(mocks.message.error).toHaveBeenCalledWith('数据库锁了')
    expect(api.selectedKeys.value).toEqual([1, 2])
    expect(afterMutate).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })
})

describe('批量删除的补充分支', () => {
  it('没有被挡下的账号：提示纯成功文案，并刷新覆盖率', async () => {
    mocks.batchDelete.mockResolvedValue({ deleted: 3, blocked: [] })
    const { api, afterMutate } = setup()
    api.onSelectionChange([1, 2, 3])

    await api.batchDelete()

    expect(mocks.batchDelete).toHaveBeenCalledWith([1, 2, 3])
    expect(mocks.message.success).toHaveBeenCalledWith('已删除 3 位')
    expect(mocks.message.warning).not.toHaveBeenCalled()
    expect(mocks.avatarCoverage).toHaveBeenCalled()
    expect(afterMutate).toHaveBeenCalled()
    expect(api.selectedKeys.value).toEqual([])
  })

  it('请求失败：提示并保留选择，busy 复位', async () => {
    mocks.batchDelete.mockRejectedValue({ response: { data: { detail: '接口 500' } } })
    const { api } = setup()
    api.onSelectionChange([5])

    await api.batchDelete()

    expect(mocks.message.error).toHaveBeenCalledWith('接口 500')
    expect(api.selectedKeys.value).toEqual([5])
    expect(api.busy.value).toBe(false)
  })
})

describe('清除所选头像的补充分支', () => {
  it('cleared>0：提示清除数量并刷新', async () => {
    mocks.clearAvatars.mockResolvedValue({ cleared: 2 })
    const { api, afterMutate } = setup()
    api.onSelectionChange([1, 2])

    await api.clearSelectedAvatars()

    expect(mocks.message.success).toHaveBeenCalledWith('已清除 2 个头像')
    expect(api.selectedKeys.value).toEqual([])
    expect(afterMutate).toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })

  it('请求失败：提示清除失败，不假成功', async () => {
    mocks.clearAvatars.mockRejectedValue({ response: { data: { detail: '文件删除失败' } } })
    const { api } = setup()
    api.onSelectionChange([1])

    await api.clearSelectedAvatars()

    expect(mocks.message.error).toHaveBeenCalledWith('文件删除失败')
    expect(mocks.message.success).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })
})

describe('同名建议弹窗', () => {
  it('加载成功：打开弹窗、写入分组、loading 复位', async () => {
    const data = { groups: [groupOf([{ id: 1, name: '甲', count: 3 }])], total: 1 }
    mocks.duplicateNames.mockResolvedValue(data)
    const { api } = setup()

    await api.openDuplicates()

    expect(api.duplicateOpen.value).toBe(true)
    expect(api.duplicateGroups.value).toEqual(data.groups)
    expect(api.duplicateLoading.value).toBe(false)
  })

  it('加载失败：提示、清空分组、弹窗仍打开（便于重试）', async () => {
    mocks.duplicateNames.mockRejectedValue({ response: { data: { detail: '聚类超时' } } })
    const { api } = setup()
    api.duplicateGroups.value = [groupOf([{ id: 1, name: '甲', count: 1 }])]

    await api.openDuplicates()

    expect(mocks.message.error).toHaveBeenCalledWith('聚类超时')
    expect(api.duplicateGroups.value).toEqual([])
    expect(api.duplicateOpen.value).toBe(true)
    expect(api.duplicateLoading.value).toBe(false)
  })
})

describe('同名合并 / 删空壳的边界', () => {
  it('组内不足 2 人：直接返回 false，不发请求', async () => {
    const { api } = setup()

    await expect(api.mergeGroup(groupOf([{ id: 1, name: '独苗', count: 2 }]))).resolves.toBe(false)

    expect(mocks.linkGroup).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })

  it('合并失败：提示、返回 false、保留分组不清空', async () => {
    mocks.linkGroup.mockRejectedValue({ response: { data: { detail: '组已存在' } } })
    const { api } = setup()
    const group = groupOf([
      { id: 1, name: '甲', count: 5 },
      { id: 2, name: '乙', count: 1 },
    ])
    api.duplicateGroups.value = [group]

    await expect(api.mergeGroup(group)).resolves.toBe(false)

    expect(mocks.message.error).toHaveBeenCalledWith('组已存在')
    expect(api.duplicateGroups.value).toHaveLength(1)
    expect(api.busy.value).toBe(false)
  })

  it('删空壳：组内账号都还有素材时只提示，不发删除请求', async () => {
    const { api } = setup()
    const group = groupOf([
      { id: 1, name: '甲', count: 2 },
      { id: 2, name: '乙', count: 1 },
    ])

    await api.removeEmptyAccounts(group)

    expect(mocks.message.info).toHaveBeenCalledWith('这组账号都还有素材，不能直接删除')
    expect(mocks.batchDelete).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })

  it('删空壳失败：提示「删除空壳账号失败」并复位 busy', async () => {
    mocks.batchDelete.mockRejectedValue({ response: { data: { detail: '权限不足' } } })
    const { api } = setup()
    const group = groupOf([
      { id: 1, name: '甲', count: 0 },
      { id: 2, name: '乙', count: 4 },
    ])

    await api.removeEmptyAccounts(group)

    expect(mocks.message.error).toHaveBeenCalledWith('权限不足')
    expect(api.busy.value).toBe(false)
  })
})
