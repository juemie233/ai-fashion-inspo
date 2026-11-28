/**
 * 随机评分（useRandomRating）**补齐用例**：既有用例覆盖了「未评分优先/回退全部/删除」，
 * 本文件补：
 *
 * - 抽到当前图或最近 12 张时的重抽逻辑（最多重抽 3 次，避免连点看到同一张、也不能死循环）；
 * - 最近 12 张的去重窗口：超过 12 张后最旧的 id 被移出，可以再次出现；
 * - 加载中的并发保护与失败提示；
 * - 评分成功（含 0 星清除）、失败、空 item 保护；
 * - removeCurrent 的「没有当前图 / 删除中」保护与指定 id/reason 透传。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  updateRating: vi.fn(),
  moveToTrash: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({
  fetchInspirations: mocks.fetchInspirations,
  updateRating: mocks.updateRating,
  moveToTrash: mocks.moveToTrash,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useRandomRating } from '../useRandomRating'

function item(id: string) {
  return {
    id,
    file_path: `images/${id}.jpg`,
    media_type: 'image',
    source_type: 'douyin',
    is_favorite: false,
    rating: 0,
    created_at: '2026-11-10T12:00:00Z',
    tags: [],
  }
}

function page(ids: string[], total = ids.length) {
  return { items: ids.map(item), total }
}

let nextItemSeq = 0
const itemOf = () => item(`img-${++nextItemSeq}`)

beforeEach(() => {
  nextItemSeq = 0
  vi.clearAllMocks()
  Object.values(mocks.message).forEach((fn) => fn.mockReset())
  mocks.fetchInspirations.mockReset()
  mocks.updateRating.mockReset()
  mocks.moveToTrash.mockReset()
  mocks.fetchInspirations.mockResolvedValue(page([]))
  mocks.updateRating.mockImplementation(async (id: string, rating: number) => ({
    ...item(id),
    rating,
  }))
  mocks.moveToTrash.mockResolvedValue({})
})

describe('重抽去重', () => {
  it('抽到与当前相同的图片时重抽，直到拿到不同的', async () => {
    const first = itemOf()
    const second = itemOf()
    mocks.fetchInspirations
      .mockResolvedValueOnce(page([first.id])) // 首次
      .mockResolvedValueOnce(page([first.id])) // 第二次：与当前重复 → 重抽
      .mockResolvedValueOnce(page([second.id])) // 重抽命中新图
    const api = useRandomRating()

    await api.loadRandom()
    expect(api.item.value?.id).toBe(first.id)

    await api.loadRandom()
    expect(api.item.value?.id).toBe(second.id)
    // 第二次加载共请求 3 次（1 次原抽 + 2 次重抽）
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(3)
  })

  it('总是抽到同一张时最多重抽 3 次（共 4 次请求）后接受重复，不死循环', async () => {
    const same = itemOf()
    mocks.fetchInspirations.mockResolvedValue(page([same.id]))
    const api = useRandomRating()

    await api.loadRandom()
    mocks.fetchInspirations.mockClear()
    await api.loadRandom()

    expect(api.item.value?.id).toBe(same.id)
    // 第 2 次加载：原抽 1 次 + 最多重抽 3 次 = 4 次请求
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(4)
  })

  it('最近 12 张去重窗口：最旧的 id 移出窗口后可再次抽到', async () => {
    const items = Array.from({ length: 13 }, () => itemOf())
    mocks.fetchInspirations
      .mockResolvedValueOnce(page([items[0].id]))
      .mockResolvedValueOnce(page([items[1].id]))
      .mockResolvedValueOnce(page([items[2].id]))
      .mockResolvedValueOnce(page([items[3].id]))
      .mockResolvedValueOnce(page([items[4].id]))
      .mockResolvedValueOnce(page([items[5].id]))
      .mockResolvedValueOnce(page([items[6].id]))
      .mockResolvedValueOnce(page([items[7].id]))
      .mockResolvedValueOnce(page([items[8].id]))
      .mockResolvedValueOnce(page([items[9].id]))
      .mockResolvedValueOnce(page([items[10].id]))
      .mockResolvedValueOnce(page([items[11].id]))
      .mockResolvedValueOnce(page([items[12].id]))
    const api = useRandomRating()

    for (let index = 0; index < 13; index += 1) await api.loadRandom()
    expect(api.item.value?.id).toBe(items[12].id)

    // 第 1 张已被挤出最近 12 张窗口 → 抽到它不会被判重，只请求 1 次
    mocks.fetchInspirations.mockClear()
    mocks.fetchInspirations.mockResolvedValueOnce(page([items[0].id]))
    await api.loadRandom()

    expect(api.item.value?.id).toBe(items[0].id)
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(1)
  })
})

describe('加载边界', () => {
  it('随机排序请求参数：首抽 unrated=true，未评分池为空时回退且不带 unrated', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(page([])) // 未评分池空
    mocks.fetchInspirations.mockResolvedValueOnce(page(['img-a'])) // 回退全部图片
    const api = useRandomRating()

    await api.loadRandom()

    expect(mocks.fetchInspirations).toHaveBeenNthCalledWith(1, {
      page: 1,
      size: 1,
      sort: 'random',
      media_type: 'image',
      unrated: true,
    })
    expect(mocks.fetchInspirations).toHaveBeenNthCalledWith(2, {
      page: 1,
      size: 1,
      sort: 'random',
      media_type: 'image',
    })
    expect(api.showingUnrated.value).toBe(false)
    expect(api.total.value).toBe(1)
  })

  it('加载中再次调用直接忽略（不会发第二个请求）', async () => {
    let resolveFirst!: (value: unknown) => void
    mocks.fetchInspirations.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )
    const api = useRandomRating()

    const first = api.loadRandom()
    await api.loadRandom() // loading 中 → 忽略
    resolveFirst(page(['img-x']))
    await first

    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(1)
    expect(api.item.value?.id).toBe('img-x')
  })

  it('接口失败：写入错误文案并复位 loading，不抛出', async () => {
    // 无 message 的错误对象 → getApiErrorMessage 回退 fallback 文案
    mocks.fetchInspirations.mockRejectedValueOnce({ response: { data: {} } })
    const api = useRandomRating()

    await expect(api.loadRandom()).resolves.toBeUndefined()

    expect(api.error.value).toBe('加载随机素材失败')
    expect(api.loading.value).toBe(false)
    expect(api.item.value).toBeNull()
  })
})

describe('评分', () => {
  it('成功：提交评分、按接口返回值合并、已评 +1、提示星级', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(page(['img-1']))
    const api = useRandomRating()
    await api.loadRandom()

    await expect(api.rate(5)).resolves.toBe(true)

    expect(mocks.updateRating).toHaveBeenCalledWith('img-1', 5)
    expect(api.item.value?.rating).toBe(5)
    expect(api.ratedCount.value).toBe(1)
    expect(mocks.message.success).toHaveBeenCalledWith('已评 5 星')
    expect(api.saving.value).toBe(false)
  })

  it('0 星用于清除评分：提示「已清除评分」', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(page(['img-2']))
    const api = useRandomRating()
    await api.loadRandom()

    await expect(api.rate(0)).resolves.toBe(true)

    expect(mocks.updateRating).toHaveBeenCalledWith('img-2', 0)
    expect(mocks.message.success).toHaveBeenCalledWith('已清除评分')
  })

  it('失败：提示「保存评分失败」，返回 false，不改已评数', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(page(['img-3']))
    mocks.updateRating.mockRejectedValueOnce({ response: { data: {} } })
    const api = useRandomRating()
    await api.loadRandom()

    await expect(api.rate(4)).resolves.toBe(false)

    expect(mocks.message.error).toHaveBeenCalledWith('保存评分失败')
    expect(api.ratedCount.value).toBe(0)
    expect(api.saving.value).toBe(false)
  })

  it('没有当前图片时不发请求、直接返回 false', async () => {
    const api = useRandomRating()

    await expect(api.rate(3)).resolves.toBe(false)

    expect(mocks.updateRating).not.toHaveBeenCalled()
    expect(api.saving.value).toBe(false)
  })
})

describe('删除当前图片的边界', () => {
  it('没有当前图且未指定 id 时直接返回 false', async () => {
    const api = useRandomRating()

    await expect(api.removeCurrent()).resolves.toBe(false)

    expect(mocks.moveToTrash).not.toHaveBeenCalled()
  })

  it('删除中再次调用直接返回 false（防连点重复请求）', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(page(['img-4']))
    let resolveDelete!: (value: unknown) => void
    mocks.moveToTrash.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDelete = resolve
        }),
    )
    const api = useRandomRating()
    await api.loadRandom()

    const first = api.removeCurrent()
    await expect(api.removeCurrent()).resolves.toBe(false)
    resolveDelete({})
    await expect(first).resolves.toBe(true)

    expect(mocks.moveToTrash).toHaveBeenCalledTimes(1)
    expect(api.deleting.value).toBe(false)
  })

  it('指定 id 与 reason 时原样透传给移入垃圾桶接口', async () => {
    const api = useRandomRating()

    await expect(api.removeCurrent('target-id', '质量差')).resolves.toBe(true)

    expect(mocks.moveToTrash).toHaveBeenCalledWith('target-id', '质量差')
    expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶')
  })
})
