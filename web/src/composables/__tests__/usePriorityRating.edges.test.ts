/**
 * 人物优先评分（usePriorityRating）**补齐用例**：既有用例覆盖主流程（加载/评分/删除/切人），
 * 本文件补未覆盖的展示计算与边界分支：
 *
 * - queuePosition / hasNextBlogger / averageText 三个展示计算（无博主/无样本/越界）；
 * - loadQueue 的并发保护；
 * - 换一张/换一位在 saving、deleting 期间的保护；
 * - 评分 0 星（清除评分）不计数、不自动换图；
 * - 队列末尾、跳过「本地已评完」博主、抽不到图时递归找下一位；
 * - 删除当前图后「评完就切人 / 抽不到图也切人」两条分支与失败提示；
 * - stop() 清掉延迟换图定时器。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

import { usePriorityRating } from '../usePriorityRating'
import type { BloggerRatingPriorityItem } from '@/api/persons'

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

beforeEach(() => {
  vi.clearAllMocks()
  Object.values(mocks.message).forEach((fn) => fn.mockReset())
  mocks.fetchRatingPriority.mockReset()
  mocks.fetchRandomUnratedImage.mockReset()
  mocks.updateRating.mockReset()
  mocks.moveToTrash.mockReset()
  vi.useFakeTimers()
  mocks.fetchRatingPriority.mockResolvedValue({ items: [blogger()], total: 1 })
  mocks.fetchRandomUnratedImage.mockResolvedValue(imagePage(['insp-1']))
  mocks.updateRating.mockImplementation(async (id: string, rating: number) => ({
    ...image(id),
    rating,
  }))
  mocks.moveToTrash.mockResolvedValue({})
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('展示计算', () => {
  it('queuePosition：无博主为空串；有队列为 n/len；越界时钳制到 len', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({ items: [], total: 0 })
    await api.loadQueue()
    expect(api.queuePosition.value).toBe('')

    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    await api.loadQueue()
    expect(api.queuePosition.value).toBe('1/2')

    api.queueIndex.value = 9
    expect(api.queuePosition.value).toBe('2/2')
  })

  it('hasNextBlogger：非末位为 true，末位为 false', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    await api.loadQueue()

    expect(api.hasNextBlogger.value).toBe(true)
    api.queueIndex.value = 1
    expect(api.hasNextBlogger.value).toBe(false)
  })

  it('averageText：无当前博主/无样本显示 --，有样本保留 1 位小数', async () => {
    const api = usePriorityRating()
    expect(api.averageText.value).toBe('--')

    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ rating_average: 4.26, rated_count: 10 })],
      total: 1,
    })
    await api.loadQueue()
    expect(api.averageText.value).toBe('4.3')

    // rating_average 为 null → 无样本
    api.currentBlogger.value!.rating_average = null
    expect(api.averageText.value).toBe('--')

    // 已评数为 0 → 即使有均值也按无样本展示
    api.currentBlogger.value!.rating_average = 4
    api.currentBlogger.value!.rated_count = 0
    expect(api.averageText.value).toBe('--')
  })
})

describe('加载与换图的并发保护', () => {
  it('loadQueue 加载中再次调用直接返回（不重复请求队列）', async () => {
    let resolveQueue!: (value: unknown) => void
    mocks.fetchRatingPriority.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveQueue = resolve
        }),
    )
    const api = usePriorityRating()

    const first = api.loadQueue()
    await api.loadQueue() // loading 中 → 忽略
    resolveQueue({ items: [blogger()], total: 1 })
    await first

    expect(mocks.fetchRatingPriority).toHaveBeenCalledTimes(1)
    expect(api.loading.value).toBe(false)
  })

  it('saving 期间 nextImage / nextBlogger 直接返回，不发抽图请求', async () => {
    const api = usePriorityRating()
    await api.loadQueue()
    const callsBefore = mocks.fetchRandomUnratedImage.mock.calls.length
    let resolveRate!: (value: unknown) => void
    mocks.updateRating.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRate = resolve
        }),
    )
    const rating = api.rate(5)

    await api.nextImage()
    await api.nextBlogger()

    expect(mocks.fetchRandomUnratedImage).toHaveBeenCalledTimes(callsBefore)
    resolveRate({ ...image('insp-1'), rating: 5 })
    await rating
  })
})

describe('评分 0 星', () => {
  it('清除评分：info 提示、不增加本次已评数、不安排自动换图', async () => {
    const api = usePriorityRating()
    mocks.fetchRandomUnratedImage.mockResolvedValue(imagePage(['insp-1']))
    await api.loadQueue()
    mocks.fetchRandomUnratedImage.mockClear()

    await expect(api.rate(0)).resolves.toBe(true)

    expect(mocks.message.info).toHaveBeenCalledWith('已清除评分')
    expect(api.ratedCount.value).toBe(0)
    await vi.advanceTimersByTimeAsync(500)
    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
  })

  it('没有当前图片时不发请求直接返回 false', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({ items: [], total: 0 })
    await api.loadQueue()

    await expect(api.rate(5)).resolves.toBe(false)
    expect(mocks.updateRating).not.toHaveBeenCalled()
  })
})

describe('自动/手动切换博主的边界', () => {
  it('跳过本地统计里已评完（unrated_count<=0）的博主', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2 }), blogger({ blogger_id: 3 })],
      total: 3,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      imagePage([`insp-b${id}`]),
    )
    await api.loadQueue()

    api.queue.value[1]!.unrated_count = 0
    await api.nextBlogger()

    expect(api.queueIndex.value).toBe(2)
    expect(api.current.value?.inspiration_id).toBe('insp-b3')
  })

  it('队列走到末尾：finished=true、current 清空、queueIndex 置 length', async () => {
    const api = usePriorityRating()
    await api.loadQueue()
    expect(api.current.value).not.toBeNull()

    await api.nextBlogger()

    expect(api.queueIndex.value).toBe(1)
    expect(api.current.value).toBeNull()
    expect(api.finished.value).toBe(true)
  })

  it('抽不到图且无报错（其实已评完）：递归找到后面还有图的博主', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2 }), blogger({ blogger_id: 3 })],
      total: 3,
    })
    mocks.fetchRandomUnratedImage
      .mockResolvedValueOnce(imagePage(['insp-b1']))
      .mockResolvedValueOnce(imagePage([])) // b2 实际已评完
      .mockResolvedValueOnce(imagePage(['insp-b3']))
    await api.loadQueue()

    await api.nextBlogger()

    expect(api.queueIndex.value).toBe(2)
    expect(api.current.value?.inspiration_id).toBe('insp-b3')
    expect(api.finished.value).toBe(false)
  })

  it('队列为空时 nextImage 不报错：current 为空并标记 finished', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({ items: [], total: 0 })
    await api.loadQueue()
    mocks.fetchRandomUnratedImage.mockClear()

    await api.nextImage()

    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
    expect(api.current.value).toBeNull()
    expect(api.finished.value).toBe(true)
  })

  it('抽图接口失败：停在当前博主，不自动跳到下一位', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    await api.loadQueue()
    mocks.fetchRandomUnratedImage.mockRejectedValueOnce({
      response: { data: { detail: '抽图 500' } },
    })

    await api.nextImage()

    expect(api.error.value).toBe('抽图 500')
    expect(api.queueIndex.value).toBe(0)
    expect(api.finished.value).toBe(false)
  })
})

describe('评分后的延迟换图', () => {
  it('当前博主最后一张评完：500ms 后自动切下一位并抽图', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1, unrated_count: 1 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      imagePage([`insp-b${id}`]),
    )
    await api.loadQueue()
    expect(api.queueIndex.value).toBe(0)

    await api.rate(5)
    await vi.advanceTimersByTimeAsync(500)

    expect(api.queueIndex.value).toBe(1)
    expect(api.current.value?.inspiration_id).toBe('insp-b2')
    expect(api.finished.value).toBe(false)
  })

  it('评分后同博主抽不到图：自动切下一位', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1, unrated_count: 5 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    mocks.fetchRandomUnratedImage
      .mockResolvedValueOnce(imagePage(['insp-b1']))
      .mockResolvedValueOnce(imagePage([])) // 同博主抽不到
      .mockResolvedValueOnce(imagePage(['insp-b2']))
    await api.loadQueue()

    await api.rate(4)
    await vi.advanceTimersByTimeAsync(500)

    expect(api.queueIndex.value).toBe(1)
    expect(api.current.value?.inspiration_id).toBe('insp-b2')
  })

  it('延迟换图前队列被清空：不崩、不再抽图', async () => {
    const api = usePriorityRating()
    await api.loadQueue()
    await api.rate(5)

    api.queue.value = []
    mocks.fetchRandomUnratedImage.mockClear()
    await vi.advanceTimersByTimeAsync(500)

    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
  })

  it('stop() 清掉延迟换图定时器（离开页面不再调接口）', async () => {
    const api = usePriorityRating()
    await api.loadQueue()
    await api.rate(5)
    mocks.fetchRandomUnratedImage.mockClear()

    api.stop()
    await vi.advanceTimersByTimeAsync(500)

    expect(mocks.fetchRandomUnratedImage).not.toHaveBeenCalled()
  })
})

describe('删除当前图片的切换分支', () => {
  it('删完当前博主已评完：直接切下一位', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [
        blogger({ blogger_id: 1, image_count: 1, rated_count: 0, unrated_count: 1 }),
        blogger({ blogger_id: 2 }),
      ],
      total: 2,
    })
    mocks.fetchRandomUnratedImage.mockImplementation(async (id: number) =>
      imagePage([`insp-b${id}`]),
    )
    await api.loadQueue()

    await expect(api.removeCurrent()).resolves.toBe(true)

    expect(mocks.moveToTrash).toHaveBeenCalledWith('insp-b1', undefined)
    expect(api.queueIndex.value).toBe(1)
    expect(api.current.value?.inspiration_id).toBe('insp-b2')
  })

  it('删完同博主抽不到图：切下一位', async () => {
    const api = usePriorityRating()
    mocks.fetchRandomUnratedImage
      .mockResolvedValueOnce(imagePage(['insp-b1']))
      .mockResolvedValueOnce(imagePage([]))
      .mockResolvedValueOnce(imagePage(['insp-b2']))
    mocks.fetchRatingPriority.mockResolvedValueOnce({
      items: [blogger({ blogger_id: 1, unrated_count: 5 }), blogger({ blogger_id: 2 })],
      total: 2,
    })
    await api.loadQueue()

    await api.removeCurrent()

    expect(api.queueIndex.value).toBe(1)
    expect(api.current.value?.inspiration_id).toBe('insp-b2')
  })

  it('删除失败：提示、返回 false、保留当前图、deleting 复位', async () => {
    const api = usePriorityRating()
    await api.loadQueue()
    mocks.moveToTrash.mockRejectedValueOnce({ response: { data: { detail: '文件被占用' } } })

    await expect(api.removeCurrent()).resolves.toBe(false)

    expect(mocks.message.error).toHaveBeenCalledWith('文件被占用')
    expect(api.current.value?.inspiration_id).toBe('insp-1')
    expect(api.deleting.value).toBe(false)
  })

  it('没有当前图且未指定 id：直接返回 false，不发删除请求', async () => {
    const api = usePriorityRating()
    mocks.fetchRatingPriority.mockResolvedValueOnce({ items: [], total: 0 })
    await api.loadQueue()

    await expect(api.removeCurrent()).resolves.toBe(false)
    expect(mocks.moveToTrash).not.toHaveBeenCalled()
  })
})
