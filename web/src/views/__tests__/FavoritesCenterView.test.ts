/**
 * 收藏中心视图测试：统计卡、最近收藏网格、收藏动态渲染与取消收藏。
 *
 * 重点不是像素，而是「数据有没有接上」：
 * - overview 一次拿统计+最近收藏；history 独立加载，失败不拖垮概览；
 * - 动态条目的动作/来源必须显示中文，时间显示 favorited_at；
 * - 点动态条目能进素材详情；最近收藏里取消收藏后概览会刷新。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  history: vi.fn(),
  toggleFavorite: vi.fn(),
  openInspiration: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/api/favorites', async () => {
  const actual = await vi.importActual<typeof import('@/api/favorites')>('@/api/favorites')
  return {
    ...actual,
    fetchFavoritesOverview: mocks.overview,
    fetchFavoritesHistory: mocks.history,
  }
})

vi.mock('@/api/inspirations', () => ({
  getFileUrl: (p: string) => `/files/${p}`,
  toggleFavorite: mocks.toggleFavorite,
}))

vi.mock('@/utils/openInspiration', () => ({
  openInspiration: mocks.openInspiration,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import FavoritesCenterView from '../FavoritesCenterView.vue'
import { formatDateTime } from '@/utils/format'

const FAVORITED_AT = '2026-11-18T20:15:00'

function recentItem(id: string) {
  return {
    id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    source_author: '小葵',
    is_favorite: true,
    favorited_at: FAVORITED_AT,
    created_at: FAVORITED_AT,
    tags: [],
  }
}

function mountView() {
  return mount(FavoritesCenterView, {
    global: {
      plugins: [ArcoVue],
      stubs: { MasonryGrid: true, FavoritesCleanupPanel: true },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.overview.mockResolvedValue({
    total_favorites: 12,
    favorites_this_week: 3,
    unrated_favorites: 4,
    unfiled_favorites: 5,
    recent: [recentItem('insp-1'), recentItem('insp-2')],
  })
  mocks.history.mockResolvedValue({
    items: [
      {
        id: 1,
        inspiration_id: 'insp-1',
        action: 'favorite',
        source: 'web',
        created_at: '2026-11-18T20:15:00',
        item: recentItem('insp-1'),
      },
      {
        id: 2,
        inspiration_id: 'insp-2',
        action: 'unfavorite',
        source: 'batch',
        created_at: '2026-11-18T20:16:00',
        item: { ...recentItem('insp-2'), is_favorite: false },
      },
    ],
  })
  mocks.toggleFavorite.mockImplementation(async (_id: string, fav: boolean) => ({
    is_favorite: fav,
    favorited_at: fav ? '2026-11-19T08:00:00Z' : null,
  }))
})

describe('FavoritesCenterView', () => {
  it('加载概览与动态：统计卡数值、最近收藏网格、动态中文动作/来源/收藏时间', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.overview).toHaveBeenCalledWith(12)
    expect(mocks.history).toHaveBeenCalledWith(30)

    const text = wrapper.text()
    expect(text).toContain('收藏中心')
    expect(text).toContain('总收藏')
    expect(text).toContain('本周新增')
    expect(text).toContain('未评分收藏')
    expect(text).toContain('未整理收藏')
    expect(text).toContain('12')
    expect(text).toContain('未评分收藏')

    const grid = wrapper.findComponent({ name: 'MasonryGrid' })
    expect((grid.props('items') as unknown[]).length).toBe(2)

    // 收藏动态：动作/来源中文 + 收藏时间
    expect(text).toContain('收藏')
    expect(text).toContain('取消收藏')
    // 来源映射与后端 FavoriteEvent.source 一致：web / batch
    expect(text).toContain('网页')
    expect(text).toContain('批量操作')
    expect(text).toContain('@小葵')
    expect(text).toContain(`收藏于 ${formatDateTime(FAVORITED_AT)}`)
  })

  it('点收藏动态条目跳素材详情', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.findAll('.fav-history-item')[0].trigger('click')
    expect(mocks.openInspiration).toHaveBeenCalledWith(expect.anything(), 'insp-1', {})
  })

  it('最近收藏里取消收藏：调用接口并刷新概览/动态', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.overview.mockClear()
    mocks.history.mockClear()

    const grid = wrapper.findComponent({ name: 'MasonryGrid' })
    grid.vm.$emit('toggle-favorite', 'insp-1')
    await flushPromises()

    expect(mocks.toggleFavorite).toHaveBeenCalledWith('insp-1', false)
    expect(mocks.message.success).toHaveBeenCalledWith('已取消收藏')
    expect(mocks.overview).toHaveBeenCalledTimes(1)
    expect(mocks.history).toHaveBeenCalledTimes(1)
  })

  it('动态加载失败显示可读错误，不伪装成空列表', async () => {
    mocks.history.mockRejectedValue({ response: { data: { detail: '动态服务异常' } } })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('动态服务异常')
  })

  it('切换「待整理」Tab 才挂载清理工作台（懒加载）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const tabs = wrapper.findAll('.arco-tabs-tab')
    const cleanupTab = tabs.find((t) => t.text().includes('待整理'))
    expect(cleanupTab).toBeTruthy()
    expect(wrapper.findComponent({ name: 'FavoritesCleanupPanel' }).exists()).toBe(false)

    await cleanupTab!.trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'FavoritesCleanupPanel' }).exists()).toBe(true)
  })
})
