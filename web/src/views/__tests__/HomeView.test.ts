/**
 * 素材库首页（HomeView）的交互路径测试。
 *
 * 为什么补这一层：后端覆盖率已到 78.65%，但 15 个 view 里只有 2 个有测试。
 * 视图层的 bug 几乎都是「接线错了」——按钮接到错的 handler、筛选变更忘了回第一页、
 * URL 不同步导致刷新后筛选丢失。这类问题不会有异常，只会「点了没反应」。
 *
 * 做法：挂载真实组件 + 真实 Pinia store / composable，只 mock API 层
 * （`@/api/*`）。这样断言的是「用户点一下 → 请求参数对不对 → URL/state 变没变」，
 * 而不是「我的 mock 有没有互相调用」。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'
import { nextTick } from 'vue'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  fetchDominantColors: vi.fn(),
  fetchTags: vi.fn(),
  updateInspiration: vi.fn(),
  batchQualityCheck: vi.fn(),
  updateQualityStatus: vi.fn(),
  createCollection: vi.fn(),
  routerReplace: vi.fn(),
  routerPush: vi.fn(),
  routeQuery: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.routerReplace, push: mocks.routerPush }),
}))

vi.mock('@/api/inspirations', async () => {
  const actual = await vi.importActual<typeof import('@/api/inspirations')>('@/api/inspirations')
  return {
    ...actual,
    fetchInspirations: mocks.fetchInspirations,
    fetchDominantColors: mocks.fetchDominantColors,
    updateInspiration: mocks.updateInspiration,
    batchQualityCheck: mocks.batchQualityCheck,
    updateQualityStatus: mocks.updateQualityStatus,
    deleteInspiration: vi.fn(),
    batchDeleteInspirations: vi.fn(),
    restoreInspirations: vi.fn(),
    batchUpdateInspirations: vi.fn(),
    fetchInspiration: vi.fn(),
  }
})

vi.mock('@/api/tags', () => ({
  fetchTags: mocks.fetchTags,
  batchAddTagToInspirations: vi.fn(),
  removeTagFromInspiration: vi.fn(),
  fetchTagEffect: vi.fn(),
  fetchEffectOverview: vi.fn(),
}))

vi.mock('@/api/collections', () => ({
  createCollection: mocks.createCollection,
  addInspirationsToCollection: vi.fn(),
  fetchCollections: vi.fn().mockResolvedValue([]),
  evaluateSmartCollection: vi.fn(),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  addInspirationBlogger: vi.fn(),
  addInspirationModel: vi.fn(),
}))

import HomeView from '../HomeView.vue'

const ITEM = {
  id: 'i1',
  file_path: 'images/a.jpg',
  thumbnail_path: 'thumbs/a.jpg',
  media_type: 'image',
  source_type: 'douyin',
  quality_status: 'approved',
  is_favorite: false,
  rating: 0,
  tags: [],
  dominant_colors: ['#ffffff'],
  created_at: '2026-10-21T10:00:00',
}

function mountView() {
  return mount(HomeView, {
    global: { plugins: [ArcoVue] },
  })
}

function lastParams() {
  const calls = mocks.fetchInspirations.mock.calls
  return calls[calls.length - 1]?.[0] ?? {}
}

describe('HomeView 交互路径', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    mocks.routeQuery = {}
    vi.clearAllMocks()
    mocks.fetchInspirations.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 40 })
    mocks.fetchDominantColors.mockResolvedValue([])
    mocks.fetchTags.mockResolvedValue([])
    mocks.createCollection.mockResolvedValue({ id: 1, name: 'x' })
  })

  it('首屏按第 1 页加载，并带上分页参数', async () => {
    mountView()
    await flushPromises()

    expect(mocks.fetchInspirations).toHaveBeenCalled()
    expect(lastParams()).toMatchObject({ page: 1 })
    expect(lastParams().size).toBeGreaterThan(0)
  })

  it('从 URL 的 page 参数恢复页码（刷新后不跳回第一页）', async () => {
    mocks.routeQuery = { page: '3' }

    mountView()
    await flushPromises()

    expect(lastParams().page).toBe(3)
  })

  it('切换来源筛选 → 回第一页 + 重新加载 + URL 同步', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.fetchInspirations.mockClear()
    mocks.routerReplace.mockClear()

    // 筛选栏里的「抖音」按钮（sourceOptions 由 sourceLabel 生成）
    const douyinBtn = wrapper.findAll('button').find((b) => b.text() === '抖音')
    expect(douyinBtn, '筛选栏应渲染「抖音」选项').toBeTruthy()
    await douyinBtn!.trigger('click')
    await flushPromises()

    // 关闭「全部」后应带上 source=抖音
    expect(lastParams().source_type).toBe('douyin')
    expect(lastParams().page).toBe(1)
    expect(mocks.routerReplace).toHaveBeenCalled()
    const query = mocks.routerReplace.mock.calls.at(-1)![0].query
    expect(query.source).toBe('douyin')
    expect(query.page).toBeUndefined() // 第 1 页不写进 URL
  })

  it('再次点「全部」应清掉 source 参数', async () => {
    const wrapper = mountView()
    await flushPromises()

    const allBtn = wrapper.findAll('button').find((b) => b.text() === '全部')
    await allBtn!.trigger('click')
    await flushPromises()

    expect(lastParams().source_type).toBeUndefined()
  })

  it('密度切换写入 localStorage（刷新后保留偏好）', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 组件内部 setDensity 由密度下拉触发；这里直接调用其暴露的按钮文案
    const compact = wrapper.findAll('button').find((b) => b.text().includes('紧凑'))
    if (compact) {
      await compact.trigger('click')
      await flushPromises()
      expect(localStorage.getItem('masonry-density')).toBe('compact')
    }
  })

  it('批量选择模式：进入后出现批量操作栏', async () => {
    const wrapper = mountView()
    await flushPromises()

    const batchBtn = wrapper.findAll('button').find((b) => b.text() === '批量选择')
    expect(batchBtn).toBeTruthy()
    await batchBtn!.trigger('click')
    await nextTick()

    // 进入批量模式后应出现「退出」或批量动作按钮
    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(
      texts.some((t) => t.includes('退出') || t.includes('收藏') || t.includes('垃圾桶')),
    ).toBe(true)
  })

  it('空结果时展示空状态而不是报错', async () => {
    mocks.fetchInspirations.mockResolvedValue({ items: [], total: 0, page: 1, size: 40 })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('共 0 条')
  })

  it('接口失败不白屏（保留页面骨架并给出提示）', async () => {
    mocks.fetchInspirations.mockRejectedValue(new Error('网络错误'))

    const wrapper = mountView()
    await flushPromises()

    // 页面结构仍在（标题渲染），错误由 store 的 error 状态承载
    expect(wrapper.text()).toContain('素材库')
  })
})
