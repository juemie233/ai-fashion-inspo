/**
 * 详情页浏览上下文（`useBrowseContext`）的测试。
 *
 * 它决定详情页「上一张/下一张」能否用、以及翻页边界是否正确：
 * - 页码从 route.query 恢复，列表参数由 buildBrowseParams 统一构建（与 HomeView 一致）；
 * - 当前素材不在上下文列表时隐藏导航（index=-1）；
 * - 页内移动直接跳相邻 id；到页边界自动请求相邻页的首/尾素材；
 * - 竞态：详情快速切换 / reset 后，旧上下文响应必须丢弃。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  buildBrowseParams: vi.fn((state: unknown, page: number, size: number) => ({
    state,
    page,
    size,
  })),
  storedBrowsePageSize: vi.fn(() => 50),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({ fetchInspirations: mocks.fetchInspirations }))
vi.mock('@/utils/browseQuery', () => ({
  buildBrowseParams: mocks.buildBrowseParams,
  storedBrowsePageSize: mocks.storedBrowsePageSize,
}))
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useBrowseContext } from '../useBrowseContext'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

function inspirationOut(id: string) {
  return { id, file_path: `images/${id}.jpg`, thumbnail_path: null } as never
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function setup(options: { query?: Record<string, string>; detailId?: string | null } = {}) {
  const route = { query: options.query ?? {} } as unknown as RouteLocationNormalizedLoaded
  const replace = vi.fn()
  const router = { replace } as unknown as Router
  const detail = ref(options.detailId === null ? null : ({ id: options.detailId ?? 'i2' } as never))
  const ctx = useBrowseContext({
    detail: detail as never,
    route,
    router,
  })
  return { ctx, detail, route, replace, router }
}

function page(items: string[], total = items.length) {
  return { items: items.map(inspirationOut), total, page: 1, size: 50 }
}

describe('useBrowseContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.storedBrowsePageSize.mockReturnValue(50)
    mocks.buildBrowseParams.mockImplementation((state: unknown, p: number, size: number) => ({
      state,
      page: p,
      size,
    }))
  })

  it('初始：页码从 route.query.page 恢复；无详情时 index=-1、导航隐藏', () => {
    const { ctx } = setup({ query: { page: '3' }, detailId: null })

    expect(ctx.browsePage.value).toBe(3)
    expect(ctx.browseIndex.value).toBe(-1)
    expect(ctx.browsePosition.value).toBe(0)
    expect(ctx.hasPrev.value).toBe(false)
    expect(ctx.hasNext.value).toBe(false)
  })

  it('load 成功：按 URL 筛选 + 页码 + 每页大小请求并回填', async () => {
    mocks.fetchInspirations.mockResolvedValue(page(['i1', 'i2', 'i3'], 120))
    const { ctx } = setup({ query: { page: '2', source: 'douyin' } })

    await ctx.load()

    expect(mocks.fetchInspirations).toHaveBeenCalledWith({
      state: { page: '2', source: 'douyin' },
      page: 2,
      size: 50,
    })
    expect(ctx.browsePage.value).toBe(2)
    expect(ctx.browseItems.value.map((i) => i.id)).toEqual(['i1', 'i2', 'i3'])
    expect(ctx.browseTotal.value).toBe(120)
    expect(ctx.browseLoading.value).toBe(false)
  })

  it('load 失败静默：清空上下文、不弹错误、loading 复位', async () => {
    mocks.fetchInspirations.mockRejectedValue(new Error('500'))
    const { ctx } = setup()

    await ctx.load()

    expect(ctx.browseItems.value).toEqual([])
    expect(ctx.browseLoading.value).toBe(false)
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('竞态：旧 load 响应晚到会被丢弃', async () => {
    const slow = deferred<ReturnType<typeof page>>()
    const fast = deferred<ReturnType<typeof page>>()
    mocks.fetchInspirations.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const { ctx } = setup()

    const first = ctx.load()
    const second = ctx.load()

    fast.resolve(page(['new'], 1))
    await second
    expect(ctx.browseItems.value.map((i) => i.id)).toEqual(['new'])

    slow.resolve(page(['stale'], 9))
    await first
    expect(ctx.browseItems.value.map((i) => i.id)).toEqual(['new'])
    expect(ctx.browseTotal.value).toBe(1)
  })

  it('reset：清空列表并使在途请求失效', async () => {
    const pending = deferred<ReturnType<typeof page>>()
    mocks.fetchInspirations.mockReturnValueOnce(pending.promise)
    const { ctx } = setup()

    const loading = ctx.load()
    ctx.reset()
    expect(ctx.browseItems.value).toEqual([])

    pending.resolve(page(['i1'], 1))
    await loading
    expect(ctx.browseItems.value).toEqual([])
  })

  it('位置与边界：首页首张可前进、可后退（有上一页）', async () => {
    mocks.fetchInspirations.mockResolvedValue(page(['i1', 'i2', 'i3'], 200))
    const { ctx } = setup({ query: { page: '2' }, detailId: 'i1' })
    await ctx.load()

    expect(ctx.browseIndex.value).toBe(0)
    expect(ctx.browsePosition.value).toBe(51) // (2-1)*50 + 0 + 1
    expect(ctx.hasPrev.value).toBe(true) // 首页首张但还有第 1 页
    expect(ctx.hasNext.value).toBe(true)
  })

  it('位置与边界：最后一张且无下一页时 hasNext=false', async () => {
    mocks.fetchInspirations.mockResolvedValue(page(['i1', 'i2'], 2))
    const { ctx } = setup({ query: { page: '1' }, detailId: 'i2' })
    await ctx.load()

    expect(ctx.browseIndex.value).toBe(1)
    expect(ctx.browsePosition.value).toBe(2)
    expect(ctx.hasPrev.value).toBe(true)
    expect(ctx.hasNext.value).toBe(false) // 1*50 < 2 为假
  })

  it('gotoItem：保留浏览 query；page>1 写入 page，page=1 删除 page', () => {
    const { ctx, replace } = setup({ query: { source: 'douyin', page: '2' } })

    ctx.gotoItem('next-id', 3)
    expect(replace).toHaveBeenLastCalledWith({
      path: '/detail/next-id',
      query: { source: 'douyin', page: '3' },
    })

    ctx.gotoItem('first-id', 1)
    expect(replace).toHaveBeenLastCalledWith({
      path: '/detail/first-id',
      query: { source: 'douyin' },
    })

    ctx.gotoItem('no-page')
    expect(replace).toHaveBeenLastCalledWith({
      path: '/detail/no-page',
      query: { source: 'douyin', page: '2' },
    })
  })

  it('页内上一张/下一张：直接跳相邻素材（不额外请求）', async () => {
    mocks.fetchInspirations.mockResolvedValue(page(['i1', 'i2', 'i3'], 3))
    const { ctx, replace } = setup({ detailId: 'i2' })
    await ctx.load()

    await ctx.goNeighbor('prev')
    expect(replace).toHaveBeenLastCalledWith({ path: '/detail/i1', query: {} })

    await ctx.goNeighbor('next')
    expect(replace).toHaveBeenLastCalledWith({ path: '/detail/i3', query: {} })
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(1) // 仅 load 的第一次
  })

  it('页边界下一张：请求下一页并跳到其首张（页码写入 query）', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce(page(['i1', 'i2'], 120))
      .mockResolvedValueOnce(page(['i51', 'i52'], 120))
    const { ctx, replace } = setup({ query: { page: '1' }, detailId: 'i2' })
    await ctx.load()

    await ctx.goNeighbor('next')

    expect(mocks.fetchInspirations).toHaveBeenLastCalledWith({
      state: { page: '1' },
      page: 2,
      size: 50,
    })
    expect(replace).toHaveBeenLastCalledWith({
      path: '/detail/i51',
      query: { page: '2' },
    })
  })

  it('页边界上一张：请求上一页并跳到其末张', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce(page(['i51', 'i52'], 120))
      .mockResolvedValueOnce(page(['i49', 'i50'], 120))
    const { ctx, replace } = setup({ query: { page: '2' }, detailId: 'i51' })
    await ctx.load()

    await ctx.goNeighbor('prev')

    // 第 1 页是默认页：query 里不保留 page
    expect(replace).toHaveBeenLastCalledWith({
      path: '/detail/i50',
      query: {},
    })
  })

  it('翻页返回空列表：info 提示且不跳转', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce(page(['i1', 'i2'], 120))
      .mockResolvedValueOnce(page([], 100))
    const { ctx, replace } = setup({ query: { page: '1' }, detailId: 'i2' })
    await ctx.load()
    replace.mockClear()

    await ctx.goNeighbor('next')

    expect(mocks.message.info).toHaveBeenCalledWith('后面没有更多素材了')
    expect(replace).not.toHaveBeenCalled()
  })

  it('翻页请求失败：提示失败且不跳转', async () => {
    mocks.fetchInspirations
      .mockResolvedValueOnce(page(['i51', 'i52'], 120))
      .mockRejectedValueOnce(new Error('500'))
    const { ctx, replace } = setup({ query: { page: '2' }, detailId: 'i51' })
    await ctx.load()
    replace.mockClear()

    await ctx.goNeighbor('prev')

    expect(mocks.message.error).toHaveBeenCalledWith('加载上一页失败')
    expect(replace).not.toHaveBeenCalled()
  })

  it('无详情 / 不在列表 / 加载中：goNeighbor 静默返回', async () => {
    const noDetail = setup({ detailId: null })
    await noDetail.ctx.goNeighbor('next')
    expect(mocks.fetchInspirations).not.toHaveBeenCalled()

    mocks.fetchInspirations.mockResolvedValue(page(['i1'], 1))
    const notInList = setup({ detailId: 'missing' })
    await notInList.ctx.load()
    await notInList.ctx.goNeighbor('next')
    expect(notInList.replace).not.toHaveBeenCalled()
  })
})
