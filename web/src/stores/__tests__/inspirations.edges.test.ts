/**
 * 素材库 store（`stores/inspirations`）补测（原 77.92%）。
 *
 * 既有 `inspirations.test.ts` 覆盖了基础 load/防乱序/收藏/垃圾桶；本文件补：
 * - load 的 `size` 覆盖与全量筛选参数透传、失败静默（logger）与过期失败丢弃；
 * - loadMore 的「加载中/没有更多」守卫、复用上次筛选参数追加、过期响应丢弃、失败日志；
 * - loadDetail / upload 的返回值与副作用；
 * - setFavorite 的并发去重（pendingFavoriteIds）、仅收藏筛选下取消即移除、item+detail 双同步；
 * - toggleFavorite 三分支（列表项/详情项/仅目标值）；
 * - setRating / remove 的局部同步。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  fetchInspiration: vi.fn(),
  uploadInspiration: vi.fn(),
  toggleFavorite: vi.fn(),
  updateRating: vi.fn(),
  moveToTrash: vi.fn(),
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({
  fetchInspirations: mocks.fetchInspirations,
  fetchInspiration: mocks.fetchInspiration,
  uploadInspiration: mocks.uploadInspiration,
  toggleFavorite: mocks.toggleFavorite,
  updateRating: mocks.updateRating,
  moveToTrash: mocks.moveToTrash,
}))
vi.mock('@/utils/logger', () => ({ logger: mocks.logger }))

import { useInspirationsStore } from '../inspirations'

interface ItemFixture {
  id: string
  is_favorite: boolean
  favorited_at: string | null
  rating: number
  [key: string]: unknown
}

function item(id: string, over: Record<string, unknown> = {}): ItemFixture {
  return { id, is_favorite: false, favorited_at: null, rating: 0, ...over }
}

/** store.items 的声明类型是 InspirationOut[]；测试 fixture 只需覆盖用到的字段 */
function setItems(store: ReturnType<typeof useInspirationsStore>, items: ItemFixture[]) {
  store.items = items as never
}

function list(items: unknown[], total = items.length, page = 1) {
  return { items, total, page, size: 50 }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('inspirations store 补测', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.fetchInspirations.mockResolvedValue(list([item('a')]))
    mocks.fetchInspiration.mockResolvedValue(item('a', { detail: true }))
    mocks.uploadInspiration.mockResolvedValue(item('new'))
    mocks.toggleFavorite.mockImplementation(async (id: string, fav: boolean) => ({
      ...item(id),
      is_favorite: fav,
      favorited_at: fav ? '2026-11-28T10:00:00Z' : null,
    }))
    mocks.updateRating.mockResolvedValue(undefined)
    mocks.moveToTrash.mockResolvedValue(undefined)
  })

  it('load：params.size 覆盖默认页大小，全部筛选参数原样透传', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(list([], 0, 2))
    const store = useInspirationsStore()

    await store.load({
      page: 2,
      size: 100,
      source_type: 'douyin',
      is_favorite: true,
      media_type: 'image',
      analysis_status: 'done',
      tag_status: 'untagged',
      quality_status: 'approved',
      is_ai_generated: true,
      include_tags: '法式',
      dominant_color: '#fff',
      date_from: '2026-11-01',
      date_to: '2026-11-30',
      ids: 'a,b',
      rating_min: 3,
      sort: 'tag_count',
    })

    expect(store.size).toBe(100)
    expect(mocks.fetchInspirations).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 2,
        size: 100,
        source_type: 'douyin',
        include_tags: '法式',
        rating_min: 3,
        sort: 'tag_count',
      }),
    )
    expect(store.page).toBe(2)
  })

  it('load 竞态：旧请求晚到被丢弃；旧请求失败也不写日志', async () => {
    const old = deferred<ReturnType<typeof list>>()
    mocks.fetchInspirations
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(list([item('new')], 1, 1))
    const store = useInspirationsStore()

    const first = store.load()
    const second = store.load()
    await second
    expect(store.items.map((i) => i.id)).toEqual(['new'])

    old.reject(new Error('stale boom'))
    await first
    expect(mocks.logger.error).not.toHaveBeenCalled()
    expect(store.items.map((i) => i.id)).toEqual(['new'])
  })

  it('load 失败：logger 记录并复位 loading（不向调用方抛）', async () => {
    mocks.fetchInspirations.mockRejectedValueOnce(new Error('500'))
    const store = useInspirationsStore()

    await expect(store.load()).resolves.toBeUndefined()

    expect(mocks.logger.error).toHaveBeenCalledWith('加载素材列表失败', expect.any(Error))
    expect(store.loading).toBe(false)
  })

  it('loadMore：加载中或已到底不发请求', async () => {
    const store = useInspirationsStore()
    setItems(store, [item('a')])
    store.total = 1
    await store.loadMore()
    expect(mocks.fetchInspirations).not.toHaveBeenCalled()

    store.total = 10
    store.loading = true
    await store.loadMore()
    expect(mocks.fetchInspirations).not.toHaveBeenCalled()
  })

  it('loadMore：复用上次筛选参数、按 page+1 追加并更新页码', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(list([item('a')], 3, 1))
    const store = useInspirationsStore()
    await store.load({ source_type: 'douyin' })
    expect(store.items).toHaveLength(1)

    mocks.fetchInspirations.mockResolvedValueOnce({
      items: [item('b')],
      total: 3,
      page: 2,
      size: 50,
    })
    await store.loadMore()

    expect(mocks.fetchInspirations).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2, source_type: 'douyin' }),
    )
    expect(store.items.map((i) => i.id)).toEqual(['a', 'b'])
    expect(store.page).toBe(2)
    expect(store.total).toBe(3)
  })

  it('loadMore 竞态：用户在途时改筛选，旧追加结果被丢弃并记录失败日志', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(list([item('a')], 5, 1))
    const store = useInspirationsStore()
    await store.load()

    const pending = deferred<{ items: unknown[]; total: number; page: number; size: number }>()
    mocks.fetchInspirations.mockReturnValueOnce(pending.promise)
    const more = store.loadMore()

    // 用户改筛选触发新的 load（请求序号自增）
    mocks.fetchInspirations.mockResolvedValueOnce(list([item('fresh')], 1, 1))
    await store.load({ source_type: 'xiaohongshu' })
    expect(store.items.map((i) => i.id)).toEqual(['fresh'])

    pending.resolve({ items: [item('stale')], total: 5, page: 2, size: 50 })
    await more
    expect(store.items.map((i) => i.id)).toEqual(['fresh'])

    mocks.fetchInspirations.mockRejectedValueOnce(new Error('boom'))
    store.total = 99
    setItems(store, [item('a')])
    await store.loadMore()
    expect(mocks.logger.error).toHaveBeenCalledWith('加载更多失败', expect.any(Error))
  })

  it('loadDetail：成功回填 currentDetail；失败静默记录日志', async () => {
    const store = useInspirationsStore()

    await store.loadDetail('a')
    expect(store.currentDetail).toEqual(item('a', { detail: true }))

    mocks.fetchInspiration.mockRejectedValueOnce(new Error('404'))
    await store.loadDetail('missing')
    expect(mocks.logger.error).toHaveBeenCalledWith('加载素材详情失败', expect.any(Error))
    expect(store.currentDetail).toEqual(item('a', { detail: true }))
  })

  it('upload：新素材插到最前并增加总数，返回上传结果', async () => {
    const store = useInspirationsStore()
    setItems(store, [item('old')])
    store.total = 1
    const formData = new FormData()

    await expect(store.upload(formData)).resolves.toEqual(item('new'))

    expect(store.items.map((i) => i.id)).toEqual(['new', 'old'])
    expect(store.total).toBe(2)
  })

  it('setFavorite：同一素材在途时重复调用被去重；成功后同步 item 与 detail', async () => {
    const store = useInspirationsStore()
    setItems(store, [item('a')])
    store.currentDetail = item('a') as never
    const pending = deferred<{ id: string; is_favorite: boolean; favorited_at: string | null }>()
    mocks.toggleFavorite.mockReturnValueOnce(pending.promise)

    const first = store.setFavorite('a', true)
    await store.setFavorite('a', true) // 第二次应直接返回（pending 去重）
    expect(mocks.toggleFavorite).toHaveBeenCalledTimes(1)
    expect(store.pendingFavoriteIds.has('a')).toBe(true)

    pending.resolve({ id: 'a', is_favorite: true, favorited_at: '2026-11-28T10:00:00Z' })
    await first

    expect((store.items[0] as ItemFixture).is_favorite).toBe(true)
    expect((store.items[0] as { favorited_at: string | null }).favorited_at).toBe(
      '2026-11-28T10:00:00Z',
    )
    expect((store.currentDetail as ItemFixture | null)?.is_favorite).toBe(true)
    expect(store.pendingFavoriteIds.has('a')).toBe(false)
  })

  it('setFavorite：仅收藏筛选下取消收藏会把条目移出列表并同步总数', async () => {
    mocks.fetchInspirations.mockResolvedValueOnce(list([item('a', { is_favorite: true })], 1))
    const store = useInspirationsStore()
    await store.load({ is_favorite: true })

    await store.setFavorite('a', false)

    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
  })

  it('setFavorite：接口失败也会清理 pending（按钮可重试，不永久灰掉）', async () => {
    mocks.toggleFavorite.mockRejectedValueOnce(new Error('500'))
    const store = useInspirationsStore()
    setItems(store, [item('a')])

    await expect(store.setFavorite('a', true)).rejects.toThrow('500')
    expect(store.pendingFavoriteIds.has('a')).toBe(false)
  })

  it('toggleFavorite：按列表项 / 详情项 / 显式目标值三种来源取反', async () => {
    const store = useInspirationsStore()
    setItems(store, [item('a', { is_favorite: false })])
    await store.toggleFavorite('a')
    expect(mocks.toggleFavorite).toHaveBeenLastCalledWith('a', true)

    setItems(store, [])
    store.currentDetail = item('b', { is_favorite: true }) as never
    await store.toggleFavorite('b')
    expect(mocks.toggleFavorite).toHaveBeenLastCalledWith('b', false)

    await store.toggleFavorite('c', true)
    expect(mocks.toggleFavorite).toHaveBeenLastCalledWith('c', true)

    // 既不在列表也不在详情、又没给目标值：静默不发请求
    mocks.toggleFavorite.mockClear()
    await store.toggleFavorite('d')
    expect(mocks.toggleFavorite).not.toHaveBeenCalled()
  })

  it('setRating：同步列表项与详情评分；remove：移出列表并减少总数', async () => {
    const store = useInspirationsStore()
    setItems(store, [item('a'), item('b')])
    store.total = 2
    store.currentDetail = item('a') as never

    await store.setRating('a', 4)
    expect(mocks.updateRating).toHaveBeenCalledWith('a', 4)
    expect((store.items[0] as ItemFixture).rating).toBe(4)
    expect((store.currentDetail as ItemFixture | null)?.rating).toBe(4)

    await store.remove('a', '质量差')
    expect(mocks.moveToTrash).toHaveBeenCalledWith('a', '质量差')
    expect(store.items.map((i) => i.id)).toEqual(['b'])
    expect(store.total).toBe(1)
  })
})
