/**
 * 人物优先评分（`usePriorityRating`）测试。
 *
 * 这页是「按人物排队逐张评图」，容易写错的地方：
 * - 队列顺序：已评占比 → 已评数 → 图片数 → id（最缺评分的必须排在前面）；
 * - 当前博主评完没自动切下一位（用户会卡在「没有新图」）；
 * - 评分后本地统计没更新（页面上已评/未评/覆盖率不动）；
 * - 接口失败却当成「评完了」自动跳走（假装成功）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchRatingPriority: vi.fn(),
  fetchRandomUnratedImage: vi.fn(),
  updateRating: vi.fn(),
  moveToTrash: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchRatingPriority: mocks.fetchRatingPriority,
    fetchRandomUnratedImage: mocks.fetchRandomUnratedImage,
  },
}))

vi.mock('@/api/inspirations', () => ({
  updateRating: mocks.updateRating,
  moveToTrash: mocks.moveToTrash,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { sortPriorityQueue, usePriorityRating } from '../usePriorityRating'
import type { BloggerRatingPriorityItem } from '@/api/persons'

/** 造一位博主的队列项 */
function blogger(overrides: Record<string, unknown> = {}): BloggerRatingPriorityItem {
  return {
    blogger_id: 1,
    name: '博主一',
    platform: 'douyin',
    avatar_path: null,
    image_count: 10,
    rated_count: 2,
    unrated_count: 8,
    rating_average: 4,
    coverage: 0.2,
    ...overrides,
  } as BloggerRatingPriorityItem
}

/** 造一张人物素材项 */
function image(id: string, overrides: Record<string, unknown> = {}) {
  return {
    inspiration_id: id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    confidence: 0,
    created_at: '2026-11-18T10:00:00Z',
    quality_status: 'pending',
    rating: 0,
    is_favorite: false,
    ...overrides,
  }
}

function imagePage(ids: string[]) {
  return {
    person: { id: 1, name: '博主一', platform: 'douyin' },
    items: ids.map((id) => image(id)),
    total: ids.length,
  }
}

function setup() {
  return usePriorityRating()
}

describe('sortPriorityQueue（优先队列排序）', () => {
  it('按已评占比升序；同占比再比已评数、图片数、id', () => {
    const sorted = sortPriorityQueue([
      blogger({ blogger_id: 4, image_count: 10, rated_count: 2 }), // 0.20
      blogger({ blogger_id: 3, image_count: 5, rated_count: 1 }), // 0.20，已评少 → 前
      blogger({ blogger_id: 2, image_count: 5, rated_count: 1, name: '同占比同已评' }), // id 较小
      blogger({ blogger_id: 1, image_count: 20, rated_count: 0 }), // 0 最缺
      blogger({ blogger_id: 5, image_count: 4, rated_count: 2 }), // 0.50 最后
    ])
    expect(sorted.map((b) => b.blogger_id)).toEqual([1, 2, 3, 4, 5])
  })
})

describe('usePriorityRating', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.fetchRatingPriority.mockResolvedValue({
      items: [blogger()],
      total: 1,
    })
    mocks.fetchRandomUnratedImage.mockResolvedValue(imagePage(['insp-1']))
    mocks.updateRating.mockImplementation(async (id: string, rating: number) => ({
      ...image(id),
      rating,
    }))
    mocks.moveToTrash.mockResolvedValue({})
  })

  it('加载队列：按优先顺序取第一位博主，并抽一张未评分图片', async () => {
    mocks.fetchRatingPriority.mockResolvedValue({
      items: [
        blogger({
          blogger_id: 2,
          name: '缺得少',
          image_count: 10,
          rated_count: 5,
          unrated_count: 5,
        }),
        blogger({
          blogger_id: 1,
          name: '最缺评分',
          image_count: 10,
          rated_count: 0,
          unrated_count: 10,
        }),
      ],
      total: 2,
    })
    const api = setup()

    await api.loadQueue()

    expect(api.queue.value.map((b) => b.blogger_id)).toEqual([1, 2])
    expect(api.currentBlogger.value?.blogger_id).toBe(1)
    expect(mocks.fetchRandomUnratedImage).toHaveBeenCalledWith(1)
    expect(api.current.value?.inspiration_id).toBe('insp-1')
    expect(api.error.value).toBeNull()
    expect(api.finished.value).toBe(false)
    vi.useRealTimers()
  })

  it('队列为空：finished=true、不抽图、不报错', async () => {
    mocks.fetchRatingPriority.mockResolvedValue({ items: [], total: 0 })
    const api = setup()

    await api.loadQueue()

    expect(api.queue.value).toEqual([])
    expect(api.finished.value).toBe(true)
    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
    expect(api.error.value).toBeNull()
    vi.useRealTimers()
  })

  it('队列接口失败：显式报错，不假装成「已评完」', async () => {
    mocks.fetchRatingPriority.mockRejectedValue({
      response: { data: { detail: '服务暂不可用' } },
    })
    const api = setup()

    await api.loadQueue()

    expect(api.error.value).toBe('服务暂不可用')
    expect(api.queue.value).toEqual([])
    expect(api.finished.value).toBe(false)
    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('评分成功：本地已评/未评/覆盖率更新，500ms 后抽同一博主的下一张', async () => {
    mocks.fetchRandomUnratedImage
      .mockResolvedValueOnce(imagePage(['insp-1']))
      .mockResolvedValueOnce(imagePage(['insp-2']))
    const api = setup()
    await api.loadQueue()

    const ok = await api.rate(5)

    expect(ok).toBe(true)
    expect(mocks.updateRating).toHaveBeenCalledWith('insp-1', 5)
    expect(api.ratedCount.value).toBe(1)
    // 10 张图片、已评 2 → 评分后已评 3、未评 7、覆盖率 0.3
    expect(api.currentBlogger.value?.rated_count).toBe(3)
    expect(api.currentBlogger.value?.unrated_count).toBe(7)
    expect(api.currentBlogger.value?.coverage).toBeCloseTo(0.3)
    expect(api.current.value?.rating).toBe(5)

    await vi.advanceTimersByTimeAsync(500)
    expect(mocks.fetchRandomUnratedImage).toHaveBeenCalledTimes(2)
    expect(api.current.value?.inspiration_id).toBe('insp-2')
    vi.useRealTimers()
  })

  it('当前博主最后一张评完：自动切下一位博主并抽 TA 的图片', async () => {
    mocks.fetchRatingPriority.mockResolvedValue({
      items: [
        blogger({
          blogger_id: 1,
          name: '最后一张',
          image_count: 2,
          rated_count: 1,
          unrated_count: 1,
        }),
        blogger({
          blogger_id: 2,
          name: '下一位',
          image_count: 8,
          rated_count: 0,
          unrated_count: 8,
        }),
      ],
      total: 2,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      id === 1 ? imagePage(['insp-1']) : imagePage(['insp-2']),
    )
    const api = setup()
    await api.loadQueue()

    await api.rate(4)
    await vi.advanceTimersByTimeAsync(500)

    expect(api.currentBlogger.value?.blogger_id).toBe(2)
    expect(mocks.fetchRandomUnratedImage).toHaveBeenLastCalledWith(2)
    expect(api.current.value?.inspiration_id).toBe('insp-2')
    expect(api.finished.value).toBe(false)
    vi.useRealTimers()
  })

  it('抽图返回空（其实已评完）：自动切下一位，不报错', async () => {
    mocks.fetchRatingPriority.mockResolvedValue({
      items: [
        blogger({ blogger_id: 1, unrated_count: 3 }),
        blogger({ blogger_id: 2, unrated_count: 5 }),
      ],
      total: 2,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      id === 1 ? imagePage([]) : imagePage(['insp-2']),
    )
    const api = setup()

    await api.loadQueue()

    expect(api.currentBlogger.value?.blogger_id).toBe(2)
    expect(api.current.value?.inspiration_id).toBe('insp-2')
    expect(api.error.value).toBeNull()
    vi.useRealTimers()
  })

  it('抽图接口失败：显式报错并停在当前博主，不自动跳走', async () => {
    mocks.fetchRandomUnratedImage.mockRejectedValue({
      response: { data: { detail: '图片服务异常' } },
    })
    const api = setup()

    await api.loadQueue()

    expect(api.error.value).toBe('图片服务异常')
    expect(api.current.value).toBeNull()
    expect(api.currentBlogger.value?.blogger_id).toBe(1)
    vi.useRealTimers()
  })

  it('评分接口失败：报错且不增加本次已评数、不自动换图', async () => {
    mocks.updateRating.mockRejectedValue({ response: { data: { detail: '保存失败' } } })
    const api = setup()
    await api.loadQueue()

    const ok = await api.rate(5)

    expect(ok).toBe(false)
    expect(mocks.message.error).toHaveBeenCalledWith('保存失败')
    expect(api.ratedCount.value).toBe(0)
    expect(api.currentBlogger.value?.rated_count).toBe(2)

    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.fetchRandomUnratedImage).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('删除当前图片：走垃圾桶并本地统计减一，然后抽下一张', async () => {
    mocks.fetchRandomUnratedImage
      .mockResolvedValueOnce(imagePage(['insp-1']))
      .mockResolvedValueOnce(imagePage(['insp-2']))
    const api = setup()
    await api.loadQueue()

    const ok = await api.removeCurrent('insp-1', '不喜欢')

    expect(ok).toBe(true)
    expect(mocks.moveToTrash).toHaveBeenCalledWith('insp-1', '不喜欢')
    expect(api.currentBlogger.value?.image_count).toBe(9)
    expect(api.currentBlogger.value?.unrated_count).toBe(7)
    expect(api.current.value?.inspiration_id).toBe('insp-2')
    vi.useRealTimers()
  })

  it('「换一位博主」直接处理下一位（跳过当前）', async () => {
    mocks.fetchRatingPriority.mockResolvedValue({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2, name: '第二位' })],
      total: 2,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      imagePage([`insp-${id}`]),
    )
    const api = setup()
    await api.loadQueue()

    await api.nextBlogger()

    expect(api.currentBlogger.value?.blogger_id).toBe(2)
    expect(api.current.value?.inspiration_id).toBe('insp-2')
    vi.useRealTimers()
  })
})
