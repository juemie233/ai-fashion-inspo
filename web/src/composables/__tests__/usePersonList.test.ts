/**
 * 人物列表列表逻辑（`usePersonList`）的测试：URL 持久化、页码修正、删除后翻页、跳详情。
 *
 * 这块的 bug 都表现为「看起来数据丢了」，而不是报错：
 *
 * 1. **列表上下文写进 URL**：页码/搜索/平台/来源/排序任一变更都要反映到地址栏，
 *    否则用户从详情页返回、或刷新页面后筛选条件全丢（默认排序还要做到「不写进 URL
 *    但能正确恢复」）；
 * 2. **非法 URL 参数回退**：`?page=abc` / `?sort=xxx` 不能让列表崩或停留在无效页；
 * 3. **删除后页码修正**：本页只剩一条且不在第一页时要回退一页（否则删完停在空页）；
 * 4. **进详情携带上下文**：详情页返回才能回到原来的筛选/页码。
 *
 * store 用真实实现（`usePersonsStore`），只 mock API 层。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  bloggerList: vi.fn(),
  bloggerStats: vi.fn(),
  bloggerRemove: vi.fn(),
  bloggerPromote: vi.fn(),
  modelList: vi.fn(),
  modelRemove: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  routeQuery: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchList: mocks.bloggerList,
    fetchStats: mocks.bloggerStats,
    remove: mocks.bloggerRemove,
    promote: mocks.bloggerPromote,
  },
  modelsApi: {
    fetchList: mocks.modelList,
    remove: mocks.modelRemove,
  },
  fetchMissingProfiles: vi.fn().mockResolvedValue({ total: 0 }),
}))

vi.mock('@/api/inspirations', () => ({
  getFileUrl: (p: string) => `/files/${p}`,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

function pageResult(items: unknown[] = [], total = items.length) {
  return { items, total, page: 1, size: 20 }
}

describe('usePersonList', () => {
  /** 原地替换 query：composable 在创建时捕获了 route 对象，重新赋值不会生效 */
  function setQuery(query: Record<string, string>) {
    for (const key of Object.keys(mocks.routeQuery)) delete mocks.routeQuery[key]
    Object.assign(mocks.routeQuery, query)
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    setQuery({})
    mocks.bloggerList.mockResolvedValue(pageResult([{ id: 1, name: '博主甲' }], 1))
    mocks.modelList.mockResolvedValue(pageResult([], 0))
    mocks.bloggerStats.mockResolvedValue({ total: 1, items: [{ platform: 'douyin', count: 1 }] })
    mocks.bloggerRemove.mockResolvedValue({})
    mocks.bloggerPromote.mockResolvedValue({})
    mocks.modelRemove.mockResolvedValue({})
  })

  async function load() {
    const mod = await import('../usePersonList')
    return mod.usePersonList('blogger')
  }

  it('默认进入「素材数最多」排序，且不把默认值写进 URL', async () => {
    const { store, loadAndSync } = await load()
    await loadAndSync()
    await Promise.resolve()

    expect(store.sort).toBe('count')
    expect(store.order).toBe('desc')
    const query = mocks.replace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query.sort).toBeUndefined() // 默认排序不写 URL
    expect(query.order).toBeUndefined()
    expect(query.kind).toBe('blogger') // kind 必须写（双 Tab 共用 /persons）
  })

  it('页码/搜索/平台/来源/非默认排序都会写进 URL', async () => {
    const { store, loadAndSync } = await load()
    await loadAndSync()

    store.page = 3
    store.search = ' 张三 '
    store.platform = 'douyin'
    store.source = 'auto_collect'
    store.sort = 'name'
    store.order = 'asc'
    await Promise.resolve()

    const query = mocks.replace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query).toMatchObject({
      kind: 'blogger',
      page: '3',
      q: '张三', // 去空白
      platform: 'douyin',
      source: 'auto_collect',
      sort: 'name',
    })
  })

  it('第一页不写 page 参数（地址栏干净）', async () => {
    const { store, loadAndSync } = await load()
    await loadAndSync()
    store.page = 1
    await Promise.resolve()

    const query = mocks.replace.mock.calls.at(-1)?.[0]?.query ?? {}
    expect(query.page).toBeUndefined()
  })

  it('从 URL 恢复上下文；非法值回退默认', async () => {
    setQuery({
      page: '2',
      q: '李四',
      platform: 'xiaohongshu',
      source: 'manual',
      sort: 'name',
      order: 'asc',
    })
    const { store, restoreFromUrl } = await load()
    restoreFromUrl()

    expect(store.page).toBe(2)
    expect(store.search).toBe('李四')
    expect(store.platform).toBe('xiaohongshu')
    expect(store.source).toBe('manual')
    expect(store.sort).toBe('name')
    expect(store.order).toBe('asc')

    // 非法参数：页码不是正整数 → 1；排序值不认识 → 默认 count
    setQuery({ page: 'abc', sort: '乱写', order: 'up' })
    restoreFromUrl()
    expect(store.page).toBe(1)
    expect(store.sort).toBe('count')
    expect(store.order).toBe('desc')
  })

  it('页码越界时自动修正到最大页并重新加载', async () => {
    // 总数 25 条、每页 20 → 只有 2 页；URL 里写 page=9
    mocks.bloggerList.mockResolvedValue({ items: [], total: 25, page: 9, size: 20 })
    const { store, loadAndSync } = await load()
    store.page = 9

    await loadAndSync()

    expect(store.page).toBe(2)
    expect(mocks.bloggerList).toHaveBeenCalledTimes(2) // 修正后又拉了一次
  })

  it('博主页加载平台分布统计；模特页不请求', async () => {
    const blogger = await load()
    await blogger.loadBloggerStats()
    expect(mocks.bloggerStats).toHaveBeenCalled()
    expect(blogger.platformCount('douyin')).toBe(1)
    expect(blogger.platformCount('不存在')).toBe(0)

    const mod = await import('../usePersonList')
    const model = mod.usePersonList('model')
    await model.loadBloggerStats()
    expect(mocks.bloggerStats).toHaveBeenCalledTimes(1) // 模特页没有这个统计
  })

  it('删除最后一条且不在第一页时回退一页，否则按当前页刷新', async () => {
    const { store, handleDelete } = await load()
    store.persons = [{ id: 1, name: '唯一一条' }] as never
    store.page = 2
    const setPage = vi.spyOn(store, 'setPage')

    await handleDelete(store.persons[0] as never)
    expect(setPage).toHaveBeenCalledWith(1)
    expect(mocks.message.success).toHaveBeenCalledWith('已删除穿搭博主「唯一一条」')

    // 本页还有多条：不翻页，直接刷新
    const again = await load()
    again.store.persons = [
      { id: 2, name: '甲' },
      { id: 3, name: '乙' },
    ] as never
    again.store.page = 2
    const loadSpy = vi.spyOn(again.store, 'load')
    await again.handleDelete(again.store.persons[0] as never)
    expect(loadSpy).toHaveBeenCalledWith(true)
  })

  it('删除失败提示后端原因，不刷新', async () => {
    mocks.bloggerRemove.mockRejectedValue(new Error('仍有素材关联'))
    const { store, handleDelete } = await load()
    const loadSpy = vi.spyOn(store, 'load')

    await handleDelete({ id: 9, name: '某人' } as never)

    expect(mocks.message.error).toHaveBeenCalled()
    expect(loadSpy).not.toHaveBeenCalled()
  })

  it('「纳入追踪」调用 promote 并刷新（提示里说明 f2 用户库前提）', async () => {
    const { store, handlePromote } = await load()
    const loadSpy = vi.spyOn(store, 'load')

    await handlePromote({ id: 5, name: '自动登记号' } as never)

    expect(mocks.bloggerPromote).toHaveBeenCalledWith(5)
    expect(loadSpy).toHaveBeenCalledWith(true)
    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('f2 用户库'))
  })

  it('跳详情时带上完整列表上下文（返回可恢复）', async () => {
    const { store, goDetail } = await load()
    store.page = 2
    store.search = '王五'
    store.platform = 'douyin'
    store.source = 'auto_collect'
    store.sort = 'name'

    goDetail({ id: 42, name: '王五' } as never)

    expect(mocks.push).toHaveBeenCalledWith({
      path: '/persons/42',
      query: {
        kind: 'blogger',
        page: '2',
        q: '王五',
        platform: 'douyin',
        source: 'auto_collect',
        sort: 'name',
      },
    })
  })

  it('搜索回车才加载；输入法组合中的回车不触发', async () => {
    const { store, onSearchKeydown } = await load()
    const reloadSpy = vi.spyOn(store, 'reload')

    onSearchKeydown(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(reloadSpy).toHaveBeenCalledTimes(1)

    onSearchKeydown({ key: 'Enter', isComposing: true } as KeyboardEvent)
    expect(reloadSpy).toHaveBeenCalledTimes(1) // 中文输入法选词时的回车不算

    onSearchKeydown(new KeyboardEvent('keydown', { key: 'a' }))
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('表头排序：只有「素材数」列可排序，其余列点击不改变排序', async () => {
    const { store, onSorterChange } = await load()

    onSorterChange('name', 'ascend') // 非素材数列：忽略
    await Promise.resolve()
    expect(store.sort).toBe('count')

    onSorterChange('inspiration_count', 'ascend') // 升序 = 找素材最少
    await Promise.resolve()
    expect(store.sort).toBe('count')
    expect(store.order).toBe('asc')

    onSorterChange('inspiration_count', 'descend')
    await Promise.resolve()
    expect(store.order).toBe('desc')
  })
})
