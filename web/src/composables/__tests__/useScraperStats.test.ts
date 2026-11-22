/**
 * 采集统计看板（`useScraperStats`）的测试。
 *
 * 看板只有「拉近 N 天聚合」一个动作，语义重点是：
 * - 默认 30 天，自定义天数要透传到 `params.days`；
 * - 失败时把 stats 清成 null（让看板显示空态，而不是继续展示上一次的旧数字）；
 * - loading 在在途期间为 true、结束后复位。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

import { useScraperStats, type ScraperStats } from '../useScraperStats'

function stats(overrides: Partial<ScraperStats> = {}): ScraperStats {
  return {
    days: 30,
    total_tasks: 12,
    completed: 10,
    failed: 2,
    success_rate: 10 / 12,
    total_found: 200,
    total_added: 80,
    by_platform: [{ platform: 'xiaohongshu', tasks: 12, found: 200, added: 80, completed: 10 }],
    by_day: [{ date: '2026-11-20', tasks: 3, added: 20, failed: 0 }],
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('useScraperStats', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('默认拉近 30 天：GET /scraper/stats?days=30 并回填', async () => {
    mocks.get.mockResolvedValue({ data: stats() })
    const board = useScraperStats()

    await board.loadStats()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/stats', { params: { days: 30 } })
    expect(board.stats.value).toEqual(stats())
    expect(board.loading.value).toBe(false)
  })

  it('自定义天数透传到 params.days', async () => {
    mocks.get.mockResolvedValue({ data: stats({ days: 7 }) })
    const board = useScraperStats()

    await board.loadStats(7)

    expect(mocks.get).toHaveBeenCalledWith('/scraper/stats', { params: { days: 7 } })
    expect(board.stats.value?.days).toBe(7)
  })

  it('在途期间 loading=true，结束后复位', async () => {
    const pending = deferred<{ data: ScraperStats }>()
    mocks.get.mockReturnValue(pending.promise)
    const board = useScraperStats()

    const promise = board.loadStats()
    expect(board.loading.value).toBe(true)

    pending.resolve({ data: stats() })
    await promise

    expect(board.loading.value).toBe(false)
  })

  it('失败：不抛错、stats 清空、loading 复位', async () => {
    mocks.get.mockResolvedValueOnce({ data: stats() })
    mocks.get.mockRejectedValueOnce(new Error('503'))
    const board = useScraperStats()

    await board.loadStats()
    expect(board.stats.value).not.toBeNull()

    await expect(board.loadStats()).resolves.toBeUndefined()

    // 失败必须清空，避免看板继续展示旧数字让人误以为是最新数据
    expect(board.stats.value).toBeNull()
    expect(board.loading.value).toBe(false)
  })

  it('先失败后成功：能正常回填', async () => {
    mocks.get.mockRejectedValueOnce(new Error('503'))
    mocks.get.mockResolvedValueOnce({ data: stats({ total_tasks: 5 }) })
    const board = useScraperStats()

    await board.loadStats()
    expect(board.stats.value).toBeNull()

    await board.loadStats(7)

    expect(board.stats.value?.total_tasks).toBe(5)
    expect(board.loading.value).toBe(false)
  })
})
