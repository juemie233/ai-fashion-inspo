/**
 * 人物列表两级排序（`usePersonList`）的测试。
 *
 * 用户确认的交互（容易在重构时改坏的规则）：
 * - 默认是「素材数降序」；第一次点表头 = 主排序，之后点另一列 = 次排序；
 * - 重复点已选列切方向；两列已满时点新列替换次排序；
 * - 主/次状态可见可清除；下拉单选排序必须清空次排序（不能留隐藏条件）；
 * - 评分排序（主或次）命中时样本不足的博主排在最后但保留在列表里，页面要有提示；
 * - sort2/order2 要进 URL，详情往返后能恢复。
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
  modelsApi: { fetchList: mocks.modelList, remove: mocks.modelRemove },
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

/** 原地替换 query：composable 创建时捕获了 route 对象，重新赋值不会生效 */
function setQuery(query: Record<string, string>) {
  for (const key of Object.keys(mocks.routeQuery)) delete mocks.routeQuery[key]
  Object.assign(mocks.routeQuery, query)
}

/** 最近一次写 URL 的 query */
function lastQuery(): Record<string, string> {
  return mocks.replace.mock.calls.at(-1)?.[0]?.query ?? {}
}

async function load() {
  const mod = await import('../usePersonList')
  return mod.usePersonList('blogger')
}

describe('usePersonList 两级排序', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    setQuery({})
    mocks.bloggerList.mockResolvedValue(pageResult([{ id: 1, name: '博主甲' }], 1))
    mocks.modelList.mockResolvedValue(pageResult([], 0))
    mocks.bloggerStats.mockResolvedValue({ total: 1, items: [] })
  })

  it('默认：主排序素材数降序、无次排序、无评分提示', async () => {
    const api = await load()
    await api.loadAndSync()

    expect(api.sortMainLabel.value).toBe('素材数 ↓')
    expect(api.sortSecondaryLabel.value).toBe('')
    expect(api.hasActiveSort.value).toBe(false)
    expect(api.ratingSortActive.value).toBe(false)
  })

  it('第一次点评分列 = 主排序，并激活评分提示', async () => {
    const api = await load()
    api.onSorterChange('rating_average', 'descend')
    await Promise.resolve()

    expect(api.store.sort).toBe('rating')
    expect(api.store.sort2).toBe('')
    expect(api.sortMainLabel.value).toBe('作品评分 ↓')
    expect(api.ratingSortActive.value).toBe(true)
    expect(api.ratingSortHint).toContain('样本不足')

    // 表头箭头只回显主排序
    const countColumn = api.columns.value.find((c) => c.dataIndex === 'inspiration_count')
    const ratingColumn = api.columns.value.find((c) => c.dataIndex === 'rating_average')
    const sortOrderOf = (col: unknown) => {
      const sortable = (col as { sortable?: { sortOrder?: string } }).sortable
      return sortable?.sortOrder
    }
    expect(sortOrderOf(ratingColumn)).toBe('descend')
    expect(sortOrderOf(countColumn)).toBe('')
  })

  it('第二次点另一列 = 次排序；状态条可见；URL 持久化 sort2', async () => {
    const api = await load()
    api.onSorterChange('rating_average', 'descend')
    await Promise.resolve()
    api.onSorterChange('inspiration_count', 'descend')
    await Promise.resolve()

    expect(api.store.sort).toBe('rating')
    expect(api.store.sort2).toBe('count')
    expect(api.store.order2).toBe('desc')
    expect(api.sortMainLabel.value).toBe('作品评分 ↓')
    expect(api.sortSecondaryLabel.value).toBe('素材数 ↓')
    expect(api.hasActiveSort.value).toBe(true)

    expect(lastQuery()).toMatchObject({ sort: 'rating', sort2: 'count' })
    // order2 默认 desc 不写进 URL（保持地址栏干净）
    expect(lastQuery().order2).toBeUndefined()

    // 主排序列有箭头、次排序列没有（按要求只回显主排序）
    const countColumn = api.columns.value.find((c) => c.dataIndex === 'inspiration_count')
    const sortable = countColumn?.sortable as { sortOrder?: string } | undefined
    expect(sortable?.sortOrder).toBe('')
  })

  it('重复点次排序列：切换次排序方向（次列无表头箭头，方向按本地状态翻转）', async () => {
    const api = await load()
    api.onSorterChange('rating_average', 'descend') // 主
    await Promise.resolve()
    api.onSorterChange('inspiration_count', 'descend') // 次
    await Promise.resolve()

    // Arco 对无箭头列每次都给 descend；本地应翻转成 asc
    api.onSorterChange('inspiration_count', 'descend')
    await Promise.resolve()
    expect(api.store.order2).toBe('asc')
    expect(api.sortSecondaryLabel.value).toBe('素材数 ↑')

    api.onSorterChange('inspiration_count', 'descend')
    await Promise.resolve()
    expect(api.store.order2).toBe('desc')

    // 次排序为评分时同理（主排序此时是 rating，点击主列按 Arco 给的方向切）
    api.onSorterChange('rating_average', 'ascend')
    await Promise.resolve()
    expect(api.store.sort).toBe('rating_asc')
  })

  it('两列已满时点新列：替换次排序而不是主排序', async () => {
    const api = await load()
    api.store.sort = 'count'
    api.store.order = 'desc'
    api.store.sort2 = 'name' // 模拟已有一个次排序（名称）
    api.onSorterChange('rating_average', 'ascend')
    await Promise.resolve()

    expect(api.store.sort).toBe('count') // 主排序不变
    expect(api.store.sort2).toBe('rating_asc') // 次排序被替换
    expect(api.sortSecondaryLabel.value).toBe('作品评分 ↑')
  })

  it('清空主排序列（Arco direction=null）：次排序提升为主排序', async () => {
    const api = await load()
    api.store.sort = 'rating'
    api.store.order = 'desc'
    api.store.sort2 = 'count'
    api.store.order2 = 'asc'

    api.onSorterChange('rating_average', null)
    await Promise.resolve()

    expect(api.store.sort).toBe('count')
    expect(api.store.order).toBe('asc')
    expect(api.store.sort2).toBe('')
    expect(api.sortSecondaryLabel.value).toBe('')
  })

  it('「清除排序」回到默认并清空次排序（评分提示消失）', async () => {
    const api = await load()
    api.store.sort = 'rating'
    api.store.sort2 = 'count'
    api.store.order2 = 'asc'

    api.clearSort()
    await Promise.resolve()

    expect(api.store.sort).toBe('count')
    expect(api.store.order).toBe('desc')
    expect(api.store.sort2).toBe('')
    expect(api.store.order2).toBe('desc')
    expect(api.hasActiveSort.value).toBe(false)
    expect(api.ratingSortActive.value).toBe(false)
    expect(mocks.bloggerList).toHaveBeenCalled()
  })

  it('下拉单选排序：清空次排序与方向，避免隐藏条件', async () => {
    const api = await load()
    api.store.sort2 = 'count'
    api.store.order2 = 'asc'
    api.store.order = 'asc'

    api.onSortPicked()

    expect(api.store.sort2).toBe('')
    expect(api.store.order2).toBe('desc')
    expect(api.store.order).toBe('desc')
    expect(mocks.bloggerList).toHaveBeenCalled()
  })

  it('评分排序只出现在次排序时也提示（样本不足排在最后）', async () => {
    const api = await load()
    api.store.sort = 'count'
    api.store.sort2 = 'rating'
    await Promise.resolve()

    expect(api.ratingSortActive.value).toBe(true)
    expect(api.ratingSortHint).toContain('样本不足')
  })

  it('URL 恢复 sort2/order2；非法 sort2 回退为空', async () => {
    setQuery({ sort: 'rating', sort2: 'count', order2: 'asc' })
    const api = await load()
    api.restoreFromUrl()

    expect(api.store.sort).toBe('rating')
    expect(api.store.sort2).toBe('count')
    expect(api.store.order2).toBe('asc')
    expect(api.sortSecondaryLabel.value).toBe('素材数 ↑')

    setQuery({ sort2: '乱写', order2: 'up' })
    api.restoreFromUrl()
    expect(api.store.sort2).toBe('')
    expect(api.store.order2).toBe('desc')
  })

  it('跳详情时携带两级排序上下文', async () => {
    const api = await load()
    api.store.sort = 'rating'
    api.store.sort2 = 'count'
    api.store.order2 = 'asc'

    api.goDetail({ id: 7, name: '博主七' } as never)

    expect(mocks.push).toHaveBeenCalledWith({
      path: '/persons/7',
      query: expect.objectContaining({ sort: 'rating', sort2: 'count', order2: 'asc' }),
    })
  })
})
