/** 随机评分页：只抽图片、评分保存、评分后自动换图。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
  updateRating: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}))

vi.mock('@/api/inspirations', async () => {
  const actual = await vi.importActual<typeof import('@/api/inspirations')>('@/api/inspirations')
  return {
    ...actual,
    fetchInspirations: mocks.fetchInspirations,
    updateRating: mocks.updateRating,
  }
})

import RandomRatingView from '../RandomRatingView.vue'

const ITEM = {
  id: 'insp-1',
  file_path: 'images/2026-11/a.jpg',
  thumbnail_path: 'thumbnails/2026-11/a.jpg',
  media_type: 'image',
  source_type: 'douyin',
  source_author: '小葵',
  is_favorite: false,
  rating: 0,
  quality_status: 'approved',
  created_at: '2026-11-10T12:00:00Z',
  tags: [],
}

function mountView() {
  return mount(RandomRatingView, { global: { plugins: [ArcoVue] } })
}

beforeEach(() => {
  vi.useFakeTimers()
  mocks.fetchInspirations.mockReset()
  mocks.updateRating.mockReset()
  mocks.routerPush.mockReset()
  let seq = 0
  mocks.fetchInspirations.mockImplementation(async () => ({
    items: [{ ...ITEM, id: `insp-${++seq}` }],
    total: 10,
    page: 1,
    size: 1,
  }))
  mocks.updateRating.mockResolvedValue({ ...ITEM, rating: 5 })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RandomRatingView', () => {
  it('只请求图片素材，并展示当前随机图片', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.fetchInspirations).toHaveBeenCalledWith(
      expect.objectContaining({
        size: 1,
        sort: 'random',
        media_type: 'image',
        unrated: true,
      }),
    )
    expect(wrapper.find('.rate-image').exists()).toBe(true)
    expect(wrapper.find('.rate-image').attributes('src')).toContain('images/2026-11/a.jpg')
    expect(wrapper.text()).toContain('小葵')
    wrapper.unmount()
  })

  it('评分调用 updateRating，并在 500ms 后自动抽下一张', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent({ name: 'Rate' }).vm.$emit('change', 5)
    await flushPromises()

    expect(mocks.updateRating).toHaveBeenCalledWith('insp-1', 5)
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(500)
    await flushPromises()
    expect(mocks.fetchInspirations).toHaveBeenCalledTimes(2)
    // 下一张回归自己的评分（不能把上一张的 5 星带过去）
    expect(wrapper.findComponent({ name: 'Rate' }).props('modelValue')).toBe(0)
    wrapper.unmount()
  })

  it('没有图片素材时显示空态，不渲染评分图', async () => {
    mocks.fetchInspirations.mockResolvedValue({ items: [], total: 0, page: 1, size: 1 })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.rate-image').exists()).toBe(false)
    expect(wrapper.text()).toContain('还没有可评分的图片素材')
    wrapper.unmount()
  })

  it('数字键 1-5 直接评分，Esc 返回素材库', async () => {
    const wrapper = mountView()
    await flushPromises()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '4' }))
    await flushPromises()
    expect(mocks.updateRating).toHaveBeenCalledWith('insp-1', 4)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(mocks.routerPush).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })
})
