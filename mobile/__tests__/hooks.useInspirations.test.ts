/** hooks/useInspirations.ts（Zustand 素材库状态）的分支测试。
 *
 * 既有用例已覆盖「收藏筛选下取消收藏」与防连点（favoriteFilter.test.ts）、
 * init 不重复拉首页（screens.states.test.tsx）。这里补齐其余分支：
 * 首屏加载/错误、快速连续加载时旧响应丢弃、筛选/排序重拉、批量收藏的
 * 全量/部分更新、批量移入垃圾桶、加载更多（成功/防护/失败/旧响应丢弃）、
 * 上传后插到最前、非收藏筛选下的收藏切换与失败后重试。
 */

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    fetchInspirationPage: jest.fn(),
    batchFavorite: jest.fn(),
    batchTrash: jest.fn(),
    uploadInspirationImage: jest.fn(),
    loadApiBaseUrl: jest.fn(),
    loadApiKey: jest.fn(),
    getApiBaseUrl: jest.fn(() => 'http://127.0.0.1:18888'),
  }
})

// logger 只用来断言「失败被记录下来」，mock 掉避免控制台噪音
jest.mock('../utils/logger', () => ({
  logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
}))

import { useInspirationStore } from '../hooks/useInspirations'
import type { Inspiration, InspirationListResponse } from '../services/api'
import { mockedApi } from '../test-utils/mockedApi'
import { logger } from '../utils/logger'

const api = mockedApi()
const loggerError = logger.error as unknown as jest.Mock

type StoreState = ReturnType<typeof useInspirationStore.getState>

const initialState: Partial<StoreState> = {
  items: [],
  total: 0,
  page: 1,
  loading: false,
  error: null,
  apiBaseUrl: 'http://127.0.0.1:18888',
  favoriteRevision: 0,
  filter: {},
  sort: 'newest',
}

function resetStore(overrides: Partial<StoreState> = {}): void {
  useInspirationStore.setState({ ...initialState, ...overrides })
}

function item(id: string, favorite = false): Inspiration {
  return {
    id,
    source_type: 'douyin',
    file_path: `images/${id}.webp`,
    media_type: 'image',
    is_favorite: favorite,
    created_at: '2026-11-19T00:00:00Z',
    tags: [],
  }
}

function pageOf(items: Inspiration[], total = items.length): InspirationListResponse {
  return { items, total, page: 1, size: 30 }
}

beforeEach(() => {
  jest.clearAllMocks()
  api.getApiBaseUrl.mockReturnValue('http://127.0.0.1:18888')
  api.loadApiBaseUrl.mockResolvedValue('http://192.168.31.142:18888')
  api.loadApiKey.mockResolvedValue('')
  api.fetchInspirationPage.mockResolvedValue(pageOf([], 0))
  resetStore()
})

describe('首屏加载', () => {
  it('加载中 loading 置真；成功后落 items/total/page 并清空错误', async () => {
    let resolvePage!: (value: InspirationListResponse) => void
    api.fetchInspirationPage.mockReturnValueOnce(
      new Promise<InspirationListResponse>((resolve) => {
        resolvePage = resolve
      }),
    )

    const pending = useInspirationStore.getState().fetchInspirations()
    expect(useInspirationStore.getState().loading).toBe(true)

    resolvePage(pageOf([item('a'), item('b', true)], 2))
    await pending

    const state = useInspirationStore.getState()
    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, {}, 'newest')
    expect(state.loading).toBe(false)
    expect(state.items.map((i) => i.id)).toEqual(['a', 'b'])
    expect(state.total).toBe(2)
    expect(state.page).toBe(1)
    expect(state.error).toBeNull()
  })

  it('首屏失败时给出重试文案并保留旧数据；记录日志', async () => {
    resetStore({ items: [item('old')], total: 1 })
    api.fetchInspirationPage.mockRejectedValueOnce(new Error('offline'))

    await useInspirationStore.getState().fetchInspirations()

    const state = useInspirationStore.getState()
    expect(state.loading).toBe(false)
    expect(state.error).toBe('加载失败，请检查网络或后端服务')
    expect(state.items.map((i) => i.id)).toEqual(['old'])
    expect(loggerError).toHaveBeenCalledWith('加载素材失败', expect.any(Error))
  })

  it('快速连续加载时旧响应不能覆盖新请求', async () => {
    let resolveOld!: (value: InspirationListResponse) => void
    api.fetchInspirationPage
      .mockReturnValueOnce(
        new Promise<InspirationListResponse>((resolve) => {
          resolveOld = resolve
        }),
      )
      .mockResolvedValueOnce(pageOf([item('new')], 1))
    const store = useInspirationStore.getState()

    const old = store.fetchInspirations()
    const fresh = store.fetchInspirations()
    await fresh

    resolveOld(pageOf([item('stale')], 5))
    await old

    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['new'])
    expect(state.total).toBe(1)
    expect(state.loading).toBe(false)
  })
})

describe('筛选与排序', () => {
  it('setFilter 整体替换条件（不合并旧条件），清空列表后重拉第一页', async () => {
    resetStore({ filter: { is_favorite: true }, items: [item('old')], total: 1, page: 3 })
    api.fetchInspirationPage.mockResolvedValueOnce(pageOf([item('new')], 1))

    await useInspirationStore.getState().setFilter({ tag_status: 'untagged' })

    const state = useInspirationStore.getState()
    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, { tag_status: 'untagged' }, 'newest')
    expect(state.filter).toEqual({ tag_status: 'untagged' })
    expect(state.items.map((i) => i.id)).toEqual(['new'])
    expect(state.page).toBe(1)
    expect(state.error).toBeNull()
  })

  it('setSort 更新排序键并按新排序重拉第一页', async () => {
    resetStore({ items: [item('old')] })
    api.fetchInspirationPage.mockResolvedValueOnce(pageOf([item('r1')], 1))

    await useInspirationStore.getState().setSort('random')

    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, {}, 'random')
    const state = useInspirationStore.getState()
    expect(state.sort).toBe('random')
    expect(state.items.map((i) => i.id)).toEqual(['r1'])
  })
})

describe('批量收藏', () => {
  it('全部更新成功时就地改本地状态，不重拉列表', async () => {
    resetStore({ items: [item('a'), item('b'), item('c')], total: 3 })
    api.batchFavorite.mockResolvedValueOnce({ updated: 2 })

    await useInspirationStore.getState().batchFavorite(['a', 'b'], true)

    const state = useInspirationStore.getState()
    expect(api.fetchInspirationPage).not.toHaveBeenCalled()
    expect(state.items.map((i) => i.is_favorite)).toEqual([true, true, false])
    expect(state.favoriteRevision).toBe(1)
  })

  it('后端只更新部分时重拉列表收敛，避免把已失效素材改成目标值', async () => {
    resetStore({ items: [item('a'), item('b')], total: 2 })
    api.batchFavorite.mockResolvedValueOnce({ updated: 1 })
    api.fetchInspirationPage.mockResolvedValueOnce(pageOf([item('a', true)], 1))

    await useInspirationStore.getState().batchFavorite(['a', 'b'], true)

    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, {}, 'newest')
    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['a'])
    expect(state.favoriteRevision).toBe(1)
  })

  it('响应没给 updated 时按全部成功处理（仍就地更新）', async () => {
    resetStore({ items: [item('a')], total: 1 })
    api.batchFavorite.mockResolvedValueOnce({})

    await useInspirationStore.getState().batchFavorite(['a'], true)

    expect(useInspirationStore.getState().items[0].is_favorite).toBe(true)
    expect(api.fetchInspirationPage).not.toHaveBeenCalled()
  })

  it('仅收藏筛选下取消收藏：重拉收敛并递增版本号', async () => {
    resetStore({ items: [item('a', true)], total: 1, filter: { is_favorite: true } })
    api.batchFavorite.mockResolvedValueOnce({ updated: 1 })
    api.fetchInspirationPage.mockResolvedValueOnce(pageOf([], 0))

    await useInspirationStore.getState().batchFavorite(['a'], false)

    expect(api.fetchInspirationPage).toHaveBeenCalledWith(1, 30, { is_favorite: true }, 'newest')
    expect(useInspirationStore.getState().items).toEqual([])
    expect(useInspirationStore.getState().favoriteRevision).toBe(1)
  })
})

describe('批量移入垃圾桶', () => {
  it('按后端返回条数扣减 total 并从列表移除', async () => {
    resetStore({ items: [item('a'), item('b'), item('c')], total: 3 })
    api.batchTrash.mockResolvedValueOnce({ trashed: 2 })

    const trashed = await useInspirationStore.getState().batchTrash(['a', 'b'])

    const state = useInspirationStore.getState()
    expect(trashed).toBe(2)
    expect(state.items.map((i) => i.id)).toEqual(['c'])
    expect(state.total).toBe(1)
  })

  it('响应缺 trashed 时按请求条数计；total 不会变负', async () => {
    resetStore({ items: [item('a')], total: 0 })
    api.batchTrash.mockResolvedValueOnce({})

    const trashed = await useInspirationStore.getState().batchTrash(['a', 'a'])

    expect(trashed).toBe(2)
    expect(useInspirationStore.getState().total).toBe(0)
  })
})

describe('加载更多（无限滚动）', () => {
  it('成功时追加下一页并推进 page', async () => {
    resetStore({ items: [item('a')], total: 3, page: 1 })
    api.fetchInspirationPage.mockResolvedValueOnce(pageOf([item('b'), item('c')], 3))

    await useInspirationStore.getState().fetchMore()

    const state = useInspirationStore.getState()
    expect(api.fetchInspirationPage).toHaveBeenCalledWith(2, 30, {}, 'newest')
    expect(state.items.map((i) => i.id)).toEqual(['a', 'b', 'c'])
    expect(state.total).toBe(3)
    expect(state.page).toBe(2)
    expect(state.loading).toBe(false)
  })

  it('已在加载中或已经到底时不再请求', async () => {
    resetStore({ items: [item('a'), item('b')], total: 2 })
    await useInspirationStore.getState().fetchMore()
    expect(api.fetchInspirationPage).not.toHaveBeenCalled()

    resetStore({ loading: true, items: [item('a')], total: 5 })
    await useInspirationStore.getState().fetchMore()
    expect(api.fetchInspirationPage).not.toHaveBeenCalled()
  })

  it('失败时保留已有数据，只标记行内错误', async () => {
    resetStore({ items: [item('a')], total: 3 })
    api.fetchInspirationPage.mockRejectedValueOnce(new Error('offline'))

    await useInspirationStore.getState().fetchMore()

    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['a'])
    expect(state.error).toBe('加载更多失败，请下拉重试')
    expect(state.loading).toBe(false)
    expect(loggerError).toHaveBeenCalledWith('加载更多失败', expect.any(Error))
  })

  it('旧分页请求失败时不影响已刷新的第一页（不写行内错误）', async () => {
    resetStore({ items: [item('a')], total: 3 })
    let rejectMore!: (reason: Error) => void
    api.fetchInspirationPage
      .mockReturnValueOnce(
        new Promise<InspirationListResponse>((_resolve, reject) => {
          rejectMore = reject
        }),
      )
      .mockResolvedValueOnce(pageOf([item('fresh')], 1))
    const store = useInspirationStore.getState()

    const more = store.fetchMore()
    const refresh = store.fetchInspirations()
    await refresh

    rejectMore(new Error('offline'))
    await more

    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['fresh'])
    expect(state.error).toBeNull()
    expect(state.loading).toBe(false)
    expect(loggerError).not.toHaveBeenCalled()
  })

  it('分页请求返回前若已刷新第一页，旧分页结果被丢弃', async () => {
    resetStore({ items: [item('a')], total: 3 })
    let resolveMore!: (value: InspirationListResponse) => void
    api.fetchInspirationPage
      .mockReturnValueOnce(
        new Promise<InspirationListResponse>((resolve) => {
          resolveMore = resolve
        }),
      )
      .mockResolvedValueOnce(pageOf([item('fresh')], 1))
    const store = useInspirationStore.getState()

    const more = store.fetchMore()
    const refresh = store.fetchInspirations()
    await refresh

    resolveMore(pageOf([item('stale')], 3))
    await more

    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.id)).toEqual(['fresh'])
    expect(state.page).toBe(1)
    expect(state.loading).toBe(false)
  })
})

describe('上传与插入', () => {
  it('上传成功后插到列表最前并递增 total，返回新素材', async () => {
    resetStore({ items: [item('old')], total: 1 })
    api.uploadInspirationImage.mockResolvedValueOnce(item('new'))

    const created = await useInspirationStore.getState().uploadImage('file:///tmp/new.jpg')

    const state = useInspirationStore.getState()
    expect(api.uploadInspirationImage).toHaveBeenCalledWith('file:///tmp/new.jpg')
    expect(state.items.map((i) => i.id)).toEqual(['new', 'old'])
    expect(state.total).toBe(2)
    expect(created.id).toBe('new')
  })
})

describe('单条收藏切换', () => {
  it('非收藏筛选下就地切换收藏状态并递增版本号', async () => {
    resetStore({ items: [item('a', false), item('b', true)], total: 2 })
    api.apiClient.patch.mockResolvedValueOnce({ data: {} })

    await useInspirationStore.getState().toggleFavorite('a', true)

    expect(api.apiClient.patch).toHaveBeenCalledWith('/inspirations/a', { is_favorite: true })
    const state = useInspirationStore.getState()
    expect(state.items.map((i) => i.is_favorite)).toEqual([true, true])
    expect(state.favoriteRevision).toBe(1)
  })

  it('请求失败后释放防连点标记，允许重试', async () => {
    resetStore({ items: [item('a', false)], total: 1 })
    api.apiClient.patch
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ data: {} })

    await expect(useInspirationStore.getState().toggleFavorite('a', true)).rejects.toThrow(
      'network',
    )
    await expect(useInspirationStore.getState().toggleFavorite('a', true)).resolves.toBeUndefined()

    expect(api.apiClient.patch).toHaveBeenCalledTimes(2)
    expect(useInspirationStore.getState().items[0].is_favorite).toBe(true)
  })
})
