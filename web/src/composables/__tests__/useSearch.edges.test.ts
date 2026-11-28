/**
 * 高级搜索（`useSearch`）补测（原 80%）。
 *
 * 既有 `useSearch.test.ts` 覆盖了基础搜索/语义/以图搜图/结果操作；本文件补未覆盖分支：
 * - 搜索历史读取坏数据回退、分页大小/筛选面板/密度持久化；
 * - URL 同步的每个 query 字段 + 复制链接成功/失败；
 * - buildQuery 的标签包含/排除、关键词、来源/媒体/分析状态/日期/评分/仅收藏；
 * - 语义搜索与以图搜图的失败提示、陈旧响应丢弃、旧 blob 预览释放；
 * - 标签筛选变化自动搜索、结果收藏（仅收藏筛选下取消即移除）与评分；
 * - 全局快捷键（/ 聚焦、Esc 退出向量模式）与卸载清理（移除监听、释放 blob URL）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, reactive } from 'vue'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  searchInspirations: vi.fn(),
  vectorSearchText: vi.fn(),
  vectorSearchImage: vi.fn(),
  toggleFavorite: vi.fn(),
  updateRating: vi.fn(),
  removeInspiration: vi.fn(),
  routerReplace: vi.fn(),
  scrollPageToTop: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  tagsStore: null as unknown,
  inspStore: null as unknown,
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
}))
vi.mock('@/stores/tags', () => ({ useTagsStore: () => mocks.tagsStore }))
vi.mock('@/stores/inspirations', () => ({ useInspirationsStore: () => mocks.inspStore }))
vi.mock('@/utils/pageScroll', () => ({ scrollPageToTop: mocks.scrollPageToTop }))
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useSearch } from '../useSearch'

interface SearchResultItem {
  id: string
  is_favorite: boolean
  favorited_at: string | null
  rating: number
}

function item(id: string, over: Partial<SearchResultItem> = {}): SearchResultItem {
  return { id, is_favorite: false, favorited_at: null, rating: 0, ...over }
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

async function flush() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

function makeTagsStore() {
  return reactive({
    selectedTags: new Set<string>(),
    excludedTags: new Set<string>(),
    combineMode: 'AND' as 'AND' | 'OR',
    load: vi.fn(async () => {}),
  })
}

type TagsStore = ReturnType<typeof makeTagsStore>

function makeInspStore() {
  return { remove: mocks.removeInspiration }
}

/** 挂载一个空组件以执行 useSearch 里的 onMounted/onUnmounted */
function mountSearch() {
  const holder: { api?: ReturnType<typeof useSearch> } = {}
  const Comp = defineComponent({
    setup() {
      holder.api = useSearch()
      return () => h('div', 'search')
    },
  })
  const wrapper = mount(Comp, { global: { plugins: [ArcoVue] } })
  if (!holder.api) throw new Error('useSearch 未初始化')
  return { api: holder.api, wrapper }
}

describe('useSearch 边界补充', () => {
  let tagsStore: TagsStore

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.searchInspirations.mockResolvedValue({ items: [item('s1')], total: 1 })
    mocks.vectorSearchText.mockResolvedValue({
      items: [{ inspiration: item('v1'), score: 0.92 }],
      total: 1,
    })
    mocks.vectorSearchImage.mockResolvedValue({
      items: [{ inspiration: item('img1'), score: 0.5 }],
      total: 1,
    })
    mocks.toggleFavorite.mockImplementation(async (id: string, fav: boolean) => ({
      ...item(id),
      is_favorite: fav,
      favorited_at: fav ? '2026-11-28T10:00:00Z' : null,
    }))
    mocks.updateRating.mockResolvedValue(undefined)
    mocks.removeInspiration.mockResolvedValue(undefined)
    tagsStore = makeTagsStore()
    mocks.tagsStore = tagsStore
    mocks.inspStore = makeInspStore()
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview') as never
    globalThis.URL.revokeObjectURL = vi.fn() as never
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('搜索历史坏数据回退空数组（非 JSON / 非数组）', () => {
    localStorage.setItem('search-history', 'not-json')
    expect(useSearch().searchHistory.value).toEqual([])

    localStorage.setItem('search-history', JSON.stringify({ a: 1 }))
    expect(useSearch().searchHistory.value).toEqual([])

    localStorage.setItem('search-history', JSON.stringify(['法式', '通勤']))
    expect(useSearch().searchHistory.value).toEqual(['法式', '通勤'])
  })

  it('分页大小 / 筛选面板可见性 / 密度写入 localStorage', async () => {
    const api = useSearch()

    api.pageSize.value = 100
    api.filterVisible.value = false
    api.density.value = 'standard'
    await nextTick()

    expect(localStorage.getItem('search-page-size')).toBe('100')
    expect(localStorage.getItem('search-filter-visible')).toBe('false')
    expect(localStorage.getItem('search-density')).toBe('standard')
  })

  it('复制搜索链接：URL 同步全部筛选字段，成功后提示', async () => {
    const api = useSearch()
    api.keyword.value = '法式'
    api.sortMode.value = 'tag_count'
    api.sourceFilter.value = 'douyin'
    api.mediaFilter.value = 'video'
    api.analysisFilter.value = 'done'
    api.dateFrom.value = '2026-11-01'
    api.dateTo.value = '2026-11-30'
    api.ratingMin.value = '4'
    api.favoriteOnly.value = true

    await api.copySearchLink()

    expect(mocks.routerReplace).toHaveBeenCalledWith({
      query: {
        q: '法式',
        sort: 'tag_count',
        source: 'douyin',
        media: 'video',
        analysis: 'done',
        from: '2026-11-01',
        to: '2026-11-30',
        rating_min: '4',
        favorite: '1',
      },
    })
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(location.href)
    expect(mocks.message.success).toHaveBeenCalledWith('已复制搜索链接')
  })

  it('复制搜索链接：剪贴板被拒时提示复制失败', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    const api = useSearch()

    await api.copySearchLink()

    expect(mocks.message.error).toHaveBeenCalledWith('复制失败')
  })

  it('doSearch：buildQuery 带全量筛选条件（标签包含/排除/组合模式）', async () => {
    tagsStore.selectedTags = new Set(['法式', '通勤'])
    tagsStore.excludedTags = new Set(['俗'])
    tagsStore.combineMode = 'OR'
    const api = useSearch()
    api.keyword.value = '裙'
    api.sortMode.value = 'rating'
    api.sourceFilter.value = 'xiaohongshu'
    api.mediaFilter.value = 'image'
    api.analysisFilter.value = 'pending'
    api.dateFrom.value = '2026-11-01'
    api.dateTo.value = '2026-11-30'
    api.ratingMin.value = '3'
    api.favoriteOnly.value = true

    await api.doSearch(2)

    expect(mocks.searchInspirations).toHaveBeenCalledWith({
      combine: 'OR',
      page: 2,
      size: 50,
      sort: 'rating',
      include_tags: '法式,通勤',
      exclude_tags: '俗',
      keyword: '裙',
      source_type: 'xiaohongshu',
      media_type: 'image',
      analysis_status: 'pending',
      date_from: '2026-11-01',
      date_to: '2026-11-30',
      rating_min: 3,
      is_favorite: true,
    })
    expect(mocks.scrollPageToTop).toHaveBeenCalled()
  })

  it('doSearch：陈旧响应不覆盖新结果；失败提示「搜索失败」', async () => {
    const old = deferred<{ items: unknown[]; total: number }>()
    mocks.searchInspirations
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ items: [item('new')], total: 1 })
    const api = useSearch()

    const first = api.doSearch()
    const second = api.doSearch()
    await second
    expect(api.results.value.map((r) => r.id)).toEqual(['new'])

    old.resolve({ items: [item('stale')], total: 9 })
    await first
    expect(api.results.value.map((r) => r.id)).toEqual(['new'])
    expect(api.searching.value).toBe(false)

    mocks.searchInspirations.mockRejectedValueOnce(new Error('500'))
    await api.doSearch()
    expect(mocks.message.error).toHaveBeenCalledWith('搜索失败')
    expect(api.searching.value).toBe(false)
  })

  it('语义搜索：空文本警告；成功进入 semantic 模式并记录历史；失败给可操作提示', async () => {
    const api = useSearch()
    await api.doSemanticSearch()
    expect(mocks.message.warning).toHaveBeenCalledWith('请输入要搜索的语义描述')
    expect(mocks.vectorSearchText).not.toHaveBeenCalled()

    api.semanticText.value = ' 米色针织开衫 '
    await api.doSemanticSearch()
    expect(mocks.vectorSearchText).toHaveBeenCalledWith('米色针织开衫', 50)
    expect(api.vectorMode.value).toBe('semantic')
    expect(api.vectorQueryLabel.value).toBe('米色针织开衫')
    expect(api.results.value.map((r) => r.id)).toEqual(['v1'])
    expect(api.searchHistory.value[0]).toBe('米色针织开衫')

    mocks.vectorSearchText.mockRejectedValueOnce({
      response: { data: { detail: '向量服务未启动' } },
    })
    await api.doSemanticSearch()
    expect(mocks.message.error).toHaveBeenCalledWith('向量服务未启动')
    expect(api.vectorLoading.value).toBe(false)
  })

  it('语义搜索陈旧响应丢弃', async () => {
    const old = deferred<{ items: unknown[]; total: number }>()
    mocks.vectorSearchText
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ items: [{ inspiration: item('new'), score: 0.9 }], total: 1 })
    const api = useSearch()
    api.semanticText.value = 'a'

    const first = api.doSemanticSearch()
    api.semanticText.value = 'b'
    await api.doSemanticSearch()
    expect(api.results.value.map((r) => r.id)).toEqual(['new'])

    old.resolve({ items: [{ inspiration: item('stale'), score: 0.1 }], total: 9 })
    await first
    expect(api.results.value.map((r) => r.id)).toEqual(['new'])
    expect(api.vectorMode.value).toBe('semantic')
  })

  it('以图搜图：释放旧预览、上传后进入 image 模式；失败提示安装 CLIP 模型', async () => {
    const api = useSearch()
    api.imagePreviewUrl.value = 'blob:old'
    const file = new File(['x'], '裙子.jpg', { type: 'image/jpeg' })

    await api.handleImagePicked(file)

    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:old')
    expect(globalThis.URL.createObjectURL).toHaveBeenCalledWith(file)
    expect(mocks.vectorSearchImage).toHaveBeenCalledWith(file, 50)
    expect(api.vectorMode.value).toBe('image')
    expect(api.vectorQueryLabel.value).toBe('裙子.jpg')
    expect(api.imagePreviewUrl.value).toBe('blob:preview')

    mocks.vectorSearchImage.mockRejectedValueOnce({})
    await api.handleImagePicked(file)
    expect(mocks.message.error).toHaveBeenCalledWith('以图搜图失败，请确认已安装 CLIP 图像模型')
    expect(api.vectorLoading.value).toBe(false)
  })

  it('标签筛选变化自动搜索：有标签就搜；清空且无关键词也搜；有关键词时不重复搜', async () => {
    const api = useSearch()
    mocks.searchInspirations.mockClear()

    tagsStore.selectedTags = new Set(['法式'])
    await nextTick()
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1)

    mocks.searchInspirations.mockClear()
    tagsStore.selectedTags = new Set()
    tagsStore.excludedTags = new Set()
    await nextTick()
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1)

    mocks.searchInspirations.mockClear()
    api.keyword.value = '法式'
    tagsStore.excludedTags = new Set(['俗'])
    await nextTick()
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1) // 有排除条件仍会搜

    mocks.searchInspirations.mockClear()
    tagsStore.excludedTags = new Set()
    await nextTick()
    expect(mocks.searchInspirations).not.toHaveBeenCalled() // 有关键词、无标签：不重复触发
  })

  it('结果删除：成功移出本地结果并提示；失败提示操作失败', async () => {
    const api = useSearch()
    api.results.value = [item('s1'), item('s2')] as never
    api.total.value = 2

    await api.handleDelete('s1')
    expect(mocks.removeInspiration).toHaveBeenCalledWith('s1')
    expect(api.results.value.map((r) => r.id)).toEqual(['s2'])
    expect(api.total.value).toBe(1)
    expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶')

    mocks.removeInspiration.mockRejectedValueOnce(new Error('500'))
    await api.handleDelete('s2')
    expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
  })

  it('结果收藏：仅收藏筛选下取消会移除；其它情况回填状态与收藏时间', async () => {
    const api = useSearch()
    api.results.value = [item('s1'), item('s2', { is_favorite: true })] as never
    api.total.value = 2
    api.favoriteOnly.value = true

    await api.handleToggleFavorite('s2')
    expect(api.results.value.map((r) => r.id)).toEqual(['s1'])
    expect(api.total.value).toBe(1)

    api.favoriteOnly.value = false
    await api.handleToggleFavorite('s1')
    expect(api.results.value[0].is_favorite).toBe(true)
    expect(api.results.value[0].favorited_at).toBe('2026-11-28T10:00:00Z')

    mocks.toggleFavorite.mockRejectedValueOnce(new Error('500'))
    await api.handleToggleFavorite('s1')
    expect(mocks.message.error).toHaveBeenCalledWith('操作失败')

    mocks.toggleFavorite.mockClear()
    await api.handleToggleFavorite('missing')
    expect(mocks.toggleFavorite).not.toHaveBeenCalled()
  })

  it('结果评分：成功提示分数/清除；失败优先后端 detail', async () => {
    const api = useSearch()
    api.results.value = [item('s1')] as never

    await api.handleRate('s1', 5)
    expect(mocks.updateRating).toHaveBeenCalledWith('s1', 5)
    expect(api.results.value[0].rating).toBe(5)
    expect(mocks.message.success).toHaveBeenCalledWith('已评分 5 星')

    await api.handleRate('s1', 0)
    expect(mocks.message.success).toHaveBeenLastCalledWith('已清除评分')

    mocks.updateRating.mockRejectedValueOnce({ response: { data: { detail: '评分越界' } } })
    await api.handleRate('s1', 9)
    expect(mocks.message.error).toHaveBeenCalledWith('评分越界')

    mocks.updateRating.mockRejectedValueOnce({})
    await api.handleRate('s1', 3)
    expect(mocks.message.error).toHaveBeenLastCalledWith('评分失败')

    mocks.updateRating.mockClear()
    await api.handleRate('missing', 3)
    expect(mocks.updateRating).not.toHaveBeenCalled()
  })

  it('搜索历史应用与清空', async () => {
    const api = useSearch()
    api.searchHistory.value = ['法式']
    mocks.searchInspirations.mockClear()

    api.applyHistory('通勤')
    await flush()
    expect(api.keyword.value).toBe('通勤')
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1)

    api.clearHistory()
    expect(api.searchHistory.value).toEqual([])
    expect(localStorage.getItem('search-history')).toBeNull()
  })

  it('全局快捷键：/ 聚焦搜索框、输入框内放行、Esc 退出向量模式', async () => {
    const { api } = mountSearch()
    await flush()
    const focus = vi.fn()
    api.searchBarRef.value = { focus }

    const slash = new KeyboardEvent('keydown', { key: '/' })
    const prevented = vi.spyOn(slash, 'preventDefault')
    document.dispatchEvent(slash)
    expect(prevented).toHaveBeenCalled()
    expect(focus).toHaveBeenCalledTimes(1)

    // 焦点在输入框内：不抢 /
    const inputTarget = document.createElement('input')
    const inInput = new KeyboardEvent('keydown', { key: '/' })
    Object.defineProperty(inInput, 'target', { value: inputTarget })
    document.dispatchEvent(inInput)
    expect(focus).toHaveBeenCalledTimes(1)

    // Esc：普通模式不动作；向量模式退出并回到普通搜索
    api.vectorMode.value = 'none'
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(api.vectorMode.value).toBe('none')

    api.vectorMode.value = 'semantic'
    mocks.searchInspirations.mockClear()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flush()
    expect(api.vectorMode.value).toBe('none')
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1)
  })

  it('卸载清理：移除全局快捷键监听并释放以图搜图 blob URL', async () => {
    const { api, wrapper } = mountSearch()
    await flush()
    const focus = vi.fn()
    api.searchBarRef.value = { focus }
    api.imagePreviewUrl.value = 'blob:pending'

    wrapper.unmount()

    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:pending')
    expect(api.imagePreviewUrl.value).toBeNull()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: '/' }))
    expect(focus).not.toHaveBeenCalled()
  })

  it('exitVectorMode：清空向量状态并回到普通搜索', async () => {
    const api = useSearch()
    api.vectorMode.value = 'image'
    api.vectorQueryLabel.value = '图'
    api.imagePreviewUrl.value = 'blob:img'
    mocks.searchInspirations.mockClear()

    api.exitVectorMode()
    await flush()

    expect(api.vectorMode.value).toBe('none')
    expect(api.vectorBadges.value).toEqual({})
    expect(api.vectorQueryLabel.value).toBe('')
    expect(api.imagePreviewUrl.value).toBeNull()
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:img')
    expect(mocks.searchInspirations).toHaveBeenCalledTimes(1)
  })
})
