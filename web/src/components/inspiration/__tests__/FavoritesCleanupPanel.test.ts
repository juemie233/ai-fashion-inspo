/**
 * 收藏中心「待整理」工作台测试：智能视图、cleanup 摘要、批量整理动作。
 *
 * 这页的价值在「点得动、算得对」：
 * - 4 个视图卡要显示后端 count；点哪个视图就按哪个 key 拉分页；
 * - cleanup 摘要可展开：缺失文件能单条取消收藏，重复组能二次确认「保留第一张」；
 * - 多选后能批量取消收藏 / 加入合集，成功后刷新视图与摘要。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  views: vi.fn(),
  viewItems: vi.fn(),
  cleanup: vi.fn(),
  batchFavorite: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/favorites', () => ({
  fetchFavoriteViews: mocks.views,
  fetchFavoriteViewItems: mocks.viewItems,
  fetchFavoritesCleanup: mocks.cleanup,
}))

vi.mock('@/api/inspirations', () => ({
  batchFavorite: mocks.batchFavorite,
  getFileUrl: (p: string) => `/files/${p}`,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import FavoritesCleanupPanel from '../FavoritesCleanupPanel.vue'
import CollectionPickerModal from '@/components/collection/CollectionPickerModal.vue'

function item(id: string) {
  return {
    id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    is_favorite: true,
    source_author: '小葵',
    created_at: '2026-11-18T20:00:00',
    tags: [],
  }
}

function cleanupFixture() {
  return {
    counts: {
      unrated: 5,
      unfiled: 3,
      missing_files: 1,
      duplicate_groups: 1,
      duplicate_items: 2,
    },
    missing_files: [item('m1')],
    duplicate_groups: [{ content_hash: 'hash-1', items: [item('d1'), item('d2')] }],
  }
}

function mountPanel() {
  return mount(FavoritesCleanupPanel, {
    global: {
      plugins: [ArcoVue],
      stubs: { MasonryGrid: true, CollectionPickerModal: true },
    },
  })
}

/** Arco 弹窗 teleport 到 body：按文案找按钮并点击 */
function clickBodyButton(text: string): void {
  const button = [...document.body.querySelectorAll('button')].find((b) =>
    b.textContent?.includes(text),
  )
  expect(button).toBeTruthy()
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.views.mockResolvedValue({
    views: [
      { key: 'unrated', label: '未评分', description: '收藏后还没打星', count: 5 },
      { key: 'unfiled', label: '未整理', description: '还没归入任何合集', count: 3 },
      { key: 'high_rating', label: '高分收藏', description: '评分 ≥4', count: 2 },
      { key: 'stale', label: '收藏超 90 天', description: '很久没回看', count: 1 },
    ],
  })
  mocks.viewItems.mockResolvedValue({
    key: 'unrated',
    items: [item('a'), item('b')],
    total: 5,
    page: 1,
    size: 24,
  })
  mocks.cleanup.mockResolvedValue(cleanupFixture())
  mocks.batchFavorite.mockResolvedValue(2)
})

describe('FavoritesCleanupPanel', () => {
  it('加载 4 个智能视图计数，并默认拉第一个视图；点击切换视图', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(mocks.views).toHaveBeenCalledTimes(1)
    expect(mocks.cleanup).toHaveBeenCalledTimes(1)
    expect(mocks.viewItems).toHaveBeenCalledWith('unrated', 1, 24)

    const text = wrapper.text()
    expect(text).toContain('未评分')
    expect(text).toContain('未整理')
    expect(text).toContain('高分收藏')
    expect(text).toContain('收藏超 90 天')

    const unfiledCard = wrapper.findAll('button').find((b) => b.text().includes('未整理'))
    await unfiledCard!.trigger('click')
    await flushPromises()
    expect(mocks.viewItems).toHaveBeenLastCalledWith('unfiled', 1, 24)
  })

  it('缺失文件：展开后单条取消收藏，成功后刷新视图/摘要', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const missingCard = wrapper.findAll('button').find((b) => b.text().includes('缺失文件'))
    await missingCard!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('@小葵')
    mocks.views.mockClear()
    mocks.cleanup.mockClear()

    const cancelButton = wrapper.findAll('button').find((b) => b.text() === '取消收藏')
    await cancelButton!.trigger('click')
    await flushPromises()

    expect(mocks.batchFavorite).toHaveBeenCalledWith(['m1'], false)
    expect(mocks.views).toHaveBeenCalled()
    expect(mocks.cleanup).toHaveBeenCalled()
  })

  it('重复组：二次确认后保留第一张，其余取消收藏', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const dupCard = wrapper.findAll('button').find((b) => b.text().includes('重复收藏'))
    await dupCard!.trigger('click')
    await flushPromises()

    const keepFirst = wrapper
      .findAll('button')
      .find((b) => b.text().includes('保留第一张，其余取消收藏'))
    await keepFirst!.trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('保留第一张，其余取消收藏？')
    clickBodyButton('确认清理')
    await flushPromises()

    // 只取消第二张及以后，第一张保留
    expect(mocks.batchFavorite).toHaveBeenCalledWith(['d2'], false)
    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('已保留第一张'))
  })

  it('多选后批量取消收藏；加入合集复用公共选择器', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const grid = wrapper.findComponent({ name: 'MasonryGrid' })
    grid.vm.$emit('toggle-select', 'a')
    await flushPromises()

    expect(wrapper.text()).toContain('已选 1 个')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '加入合集')!
      .trigger('click')
    const picker = wrapper.findComponent(CollectionPickerModal)
    expect(picker.props('inspirationIds')).toEqual(['a'])

    // 批量取消收藏
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '取消收藏')!
      .trigger('click')
    await flushPromises()
    expect(mocks.batchFavorite).toHaveBeenCalledWith(['a'], false)
  })

  it('视图素材为空时显示空态文案', async () => {
    mocks.viewItems.mockResolvedValue({ key: 'unrated', items: [], total: 0, page: 1, size: 24 })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('这个视图已经很干净了')
  })
})
