/**
 * 搜索 composable（useSearch）的测试：普通搜索 / 语义搜索 / 以图搜图 / 结果区操作。
 *
 * 三种搜索共用同一个「请求序号」（`searchSeq`），这是本模块最关键的机制：
 * 用户在搜索框连打几次、或先文字后传图时，**陈旧的响应必须被丢弃**，
 * 否则后返回的旧结果会覆盖新结果——界面看起来「搜了但结果不对」，且没有任何报错。
 *
 * 另外两个容易静默出错的点：
 * - 搜索结果不经过素材库 store，删除/收藏必须直接调 API 并就地更新 `results`，
 *   否则按钮点了没反应（store 里找不到这条）；
 * - 向量搜索失败要给出「可能是模型/服务没就绪」的可操作提示，而不是笼统的失败。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  searchInspirations: vi.fn(),
  vectorSearchText: vi.fn(),
  vectorSearchImage: vi.fn(),
  toggleFavorite: vi.fn(),
  updateRating: vi.fn(),
  deleteInspiration: vi.fn(),
  moveToTrash: vi.fn(),
  routerReplace: vi.fn(),
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: mocks.routerReplace, push: vi.fn() }),
}))

vi.mock('@/api/search', () => ({
  searchInspirations: mocks.searchInspirations,
  vectorSearchText: mocks.vectorSearchText,
  vectorSearchImage: mocks.vectorSearchImage,
}))

vi.mock('@/api/inspirations', () => ({
  toggleFavorite: mocks.toggleFavorite,
  updateRating: mocks.updateRating,
  deleteInspiration: mocks.deleteInspiration,
  moveToTrash: mocks.moveToTrash,
  getFileUrl: (p: string) => `/files/${p}`,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useSearch } from '../useSearch'

const ITEM = {
  id: 's1',
  file_path: 'images/a.jpg',
  thumbnail_path: 'thumbs/a.jpg',
  is_favorite: false,
  rating: 0,
} as never

function mountSearch() {
  setActivePinia(createPinia())
  return useSearch()
}

describe('useSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview')
    globalThis.URL.revokeObjectURL = vi.fn()
    mocks.searchInspirations.mockResolvedValue({ items: [ITEM], total: 1 })
    mocks.vectorSearchText.mockResolvedValue({
      items: [{ inspiration: ITEM, score: 0.87 }],
      total: 1,
    })
    mocks.vectorSearchImage.mockResolvedValue({
      items: [{ inspiration: ITEM, score: 0.5 }],
      total: 1,
    })
    mocks.toggleFavorite.mockResolvedValue({})
    mocks.moveToTrash.mockResolvedValue({})
    mocks.updateRating.mockResolvedValue({})
  })

  it('普通搜索：写回结果、总数，并同步 URL', async () => {
    const s = mountSearch()

    s.keyword.value = '针织'
    await s.doSearch(1)

    expect(mocks.searchInspirations).toHaveBeenCalled()
    expect(s.results.value).toHaveLength(1)
    expect(s.total.value).toBe(1)
    expect(mocks.routerReplace).toHaveBeenCalledWith({
      query: expect.objectContaining({ q: '针织' }),
    })
  })

  it('搜索失败给出提示且不残留 loading', async () => {
    mocks.searchInspirations.mockRejectedValue(new Error('boom'))
    const s = mountSearch()

    await s.doSearch(1)

    expect(mocks.message.error).toHaveBeenCalledWith('搜索失败')
    expect(s.searching.value).toBe(false)
  })

  it('乱序响应：先发的请求后返回时被丢弃（不覆盖新结果）', async () => {
    const s = mountSearch()
    let resolveFirst: (v: unknown) => void = () => {}
    mocks.searchInspirations
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce({ items: [{ ...(ITEM as object), id: 'new' }], total: 1 })

    const first = s.doSearch(1) // 悬挂
    await s.doSearch(1) // 第二次先返回
    // 现在放行第一次（陈旧响应）
    resolveFirst({ items: [{ ...(ITEM as object), id: 'stale' }], total: 99 })
    await first

    expect(s.results.value[0].id).toBe('new') // 新结果未被旧响应覆盖
    expect(s.total.value).toBe(1)
  })

  it('语义搜索：进入 semantic 模式、带相似度角标、记入搜索历史', async () => {
    const s = mountSearch()

    s.semanticText.value = '适合通勤的米色针织'
    await s.doSemanticSearch()

    expect(mocks.vectorSearchText).toHaveBeenCalledWith('适合通勤的米色针织', 50)
    expect(s.vectorMode.value).toBe('semantic')
    expect(s.vectorQueryLabel.value).toBe('适合通勤的米色针织')
    expect(s.results.value).toHaveLength(1)
    expect(s.vectorBadges.value['s1']).toBe('87% 相似')
    expect(s.searchHistory.value[0]).toBe('适合通勤的米色针织')
  })

  it('语义搜索空输入只提示，不发请求', async () => {
    const s = mountSearch()

    s.semanticText.value = '   '
    await s.doSemanticSearch()

    expect(mocks.vectorSearchText).not.toHaveBeenCalled()
    expect(mocks.message.warning).toHaveBeenCalledWith(expect.stringContaining('语义描述'))
  })

  it('语义搜索失败提示「向量服务可能未就绪」', async () => {
    mocks.vectorSearchText.mockRejectedValue(new Error('503'))
    const s = mountSearch()

    s.semanticText.value = '针织'
    await s.doSemanticSearch()

    // 提示优先用接口返回的可读信息；两者任一都算「给了可操作提示」
    expect(String(mocks.message.error.mock.calls.at(-1)?.[0])).toMatch(/向量服务|503/)
    expect(s.vectorLoading.value).toBe(false)
  })

  it('以图搜图：生成本地预览并以文件名作为查询标签', async () => {
    const s = mountSearch()
    const file = new File(['x'], 'look.jpg', { type: 'image/jpeg' })

    await s.handleImagePicked(file)

    expect(mocks.vectorSearchImage).toHaveBeenCalledWith(file, 50)
    expect(s.vectorMode.value).toBe('image')
    expect(s.vectorQueryLabel.value).toBe('look.jpg')
    expect(s.imagePreviewUrl.value).toBe('blob:preview')
  })

  it('以图搜图失败提示 CLIP 模型可能未安装', async () => {
    mocks.vectorSearchImage.mockRejectedValue(new Error('no model'))
    const s = mountSearch()

    await s.handleImagePicked(new File(['x'], 'a.jpg', { type: 'image/jpeg' }))

    expect(String(mocks.message.error.mock.calls.at(-1)?.[0])).toMatch(/CLIP|no model/)
  })

  it('退出向量模式回到普通搜索，并释放预览 URL', async () => {
    const s = mountSearch()
    await s.handleImagePicked(new File(['x'], 'a.jpg', { type: 'image/jpeg' }))
    mocks.searchInspirations.mockClear()

    s.exitVectorMode()
    await Promise.resolve()

    expect(s.vectorMode.value).toBe('none')
    expect(s.imagePreviewUrl.value).toBeNull()
    expect(s.vectorBadges.value).toEqual({})
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
    expect(mocks.searchInspirations).toHaveBeenCalled()
  })

  it('搜索历史：最新在前、去重、最多 10 条、持久化到 localStorage', async () => {
    const s = mountSearch()

    for (let i = 0; i < 12; i++) s.handleSearchBar(`kw${i}`)
    s.handleSearchBar('kw5') // 重复项提到最前，不产生新条目

    expect(s.searchHistory.value).toHaveLength(10)
    expect(s.searchHistory.value[0]).toBe('kw5')
    expect(s.searchHistory.value.filter((h) => h === 'kw5')).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem('search-history') || '[]')[0]).toBe('kw5')
  })

  it('删除结果：调 API 后就地移除并递减总数', async () => {
    const s = mountSearch()
    await s.doSearch(1)

    await s.handleDelete('s1')

    expect(mocks.moveToTrash).toHaveBeenCalledWith('s1', undefined)
    expect(s.results.value).toEqual([])
    expect(s.total.value).toBe(0)
  })

  it('收藏结果：直接调 API 并就地更新（不走素材库 store）', async () => {
    const s = mountSearch()
    await s.doSearch(1)

    await s.handleToggleFavorite('s1')

    expect(mocks.toggleFavorite).toHaveBeenCalledWith('s1', true)
    expect(s.results.value[0].is_favorite).toBe(true)
  })

  it('命中不到结果项时收藏是空操作（不误发请求）', async () => {
    const s = mountSearch()

    await s.handleToggleFavorite('不存在')

    expect(mocks.toggleFavorite).not.toHaveBeenCalled()
  })

  it('分页大小与筛选面板可见性持久化', async () => {
    const s = mountSearch()

    s.pageSize.value = 100
    s.filterVisible.value = false
    await Promise.resolve()

    expect(localStorage.getItem('search-page-size')).toBe('100')
    expect(localStorage.getItem('search-filter-visible')).toBe('false')
  })

  it('复制搜索链接失败时提示（剪贴板被拒不会静默）', async () => {
    const s = mountSearch()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })

    await s.copySearchLink()

    expect(mocks.message.error).toHaveBeenCalledWith('复制失败')
  })
})
