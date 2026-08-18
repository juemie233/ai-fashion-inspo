/** persons store 单测：mock API 模块，覆盖加载/筛选/请求序号与 kind 分流。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// 在 import store 前 mock API 模块（工厂生成 bloggersApi/modelsApi）
vi.mock('@/api/persons', () => {
  const makeApi = () => ({
    fetchList: vi.fn(),
  })
  return {
    bloggersApi: makeApi(),
    modelsApi: makeApi(),
    importBloggersCsv: vi.fn(),
  }
})

import { bloggersApi, modelsApi } from '@/api/persons'
import { usePersonsStore } from '@/stores/persons'

const mockBloggerFetch = bloggersApi.fetchList as unknown as ReturnType<typeof vi.fn>
const mockModelFetch = modelsApi.fetchList as unknown as ReturnType<typeof vi.fn>

function makePerson(id: number, name: string) {
  return {
    id,
    name,
    platform: 'other',
    inspiration_count: 0,
  }
}

describe('persons store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockBloggerFetch.mockReset()
    mockModelFetch.mockReset()
    // 静默 store 预期的错误日志（加载失败场景）
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('load 填充列表与总数', async () => {
    mockBloggerFetch.mockResolvedValue({
      items: [makePerson(1, '博主甲'), makePerson(2, '博主乙')],
      total: 2,
      page: 1,
      size: 20,
    })
    const store = usePersonsStore('blogger')
    await store.load(true)

    expect(store.persons).toHaveLength(2)
    expect(store.total).toBe(2)
    expect(mockBloggerFetch).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, size: 20 })
    )
  })

  it('博主与模特使用各自 API（kind 分流）', async () => {
    mockBloggerFetch.mockResolvedValue({ items: [makePerson(1, '博主')], total: 1, page: 1, size: 20 })
    mockModelFetch.mockResolvedValue({ items: [makePerson(2, '模特')], total: 1, page: 1, size: 20 })

    const bloggerStore = usePersonsStore('blogger')
    const modelStore = usePersonsStore('model')
    await bloggerStore.load(true)
    await modelStore.load(true)

    expect(mockBloggerFetch).toHaveBeenCalledTimes(1)
    expect(mockModelFetch).toHaveBeenCalledTimes(1)
    expect(bloggerStore.persons[0].name).toBe('博主')
    expect(modelStore.persons[0].name).toBe('模特')
  })

  it('load 失败设置 error，不污染旧数据', async () => {
    const store = usePersonsStore('blogger')
    mockBloggerFetch.mockRejectedValueOnce(new Error('network'))
    await store.load(true)

    expect(store.error).toContain('加载人物列表失败')
    expect(store.persons).toEqual([])
    expect(store.loading).toBe(false)
  })

  it('筛选条件传入 API 参数', async () => {
    mockBloggerFetch.mockResolvedValue({ items: [], total: 0, page: 1, size: 20 })
    const store = usePersonsStore('blogger')
    store.search = '小美'
    store.platform = 'xiaohongshu'
    store.sort = 'count'

    await store.load(true)

    expect(mockBloggerFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        search: '小美',
        platform: 'xiaohongshu',
        sort: 'count',
      })
    )
  })

  it('reload 重置页码到第一页', async () => {
    mockBloggerFetch.mockResolvedValue({ items: [], total: 0, page: 1, size: 20 })
    const store = usePersonsStore('blogger')
    store.page = 5
    await store.reload()

    expect(store.page).toBe(1)
    expect(mockBloggerFetch).toHaveBeenCalledWith(expect.objectContaining({ page: 1 }))
  })

  it('请求序号：过期响应不覆盖新数据', async () => {
    const store = usePersonsStore('blogger')
    let resolveOld: (v: unknown) => void
    mockBloggerFetch
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          })
      )
      .mockResolvedValueOnce({
        items: [makePerson(9, '最新结果')],
        total: 1,
        page: 1,
        size: 20,
      })

    const p1 = store.load(true)  // 第一次请求（挂起）
    await store.load(true)       // 第二次请求（立即返回）
    await new Promise((r) => setTimeout(r, 0))

    // 过期响应后到：不应覆盖新列表
    resolveOld!({ items: [makePerson(1, '过期结果')], total: 1, page: 1, size: 20 })
    await p1

    expect(store.persons[0].name).toBe('最新结果')
    expect(store.persons[0].name).not.toBe('过期结果')
  })
})
