/**
 * 人物优先评分页（`PersonRatingView`）最小回归。
 *
 * 覆盖：博主档案/统计渲染（无样本平均分显示 --）、大图、键盘 1-5 评分 / Esc 返回、
 * 删除走 TrashReasonModal、以及「换一位博主」入口。数据逻辑在 usePriorityRating 的
 * 单测里，这里只锁页面接线与文案。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  loadQueue: vi.fn(),
  nextImage: vi.fn(),
  nextBlogger: vi.fn(),
  rate: vi.fn(),
  removeCurrent: vi.fn(),
  stop: vi.fn(),
  api: null as unknown,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}))

vi.mock('@/composables/usePriorityRating', async () => {
  const { ref, computed } = await import('vue')
  const currentBlogger = ref<Record<string, unknown> | null>(null)
  const current = ref<Record<string, unknown> | null>(null)
  const api = {
    queue: ref<unknown[]>([]),
    queueTotal: ref(2),
    currentBlogger,
    current,
    loading: ref(false),
    saving: ref(false),
    deleting: ref(false),
    ratedCount: ref(0),
    error: ref<string | null>(null),
    finished: ref(false),
    queuePosition: computed(() => '1/2'),
    hasNextBlogger: ref(true),
    averageText: computed(() => {
      const blogger = currentBlogger.value as {
        rating_average?: number | null
        rated_count?: number
      } | null
      if (!blogger) return '--'
      return blogger.rating_average == null || (blogger.rated_count ?? 0) <= 0
        ? '--'
        : Number(blogger.rating_average).toFixed(1)
    }),
    loadQueue: mocks.loadQueue,
    nextImage: mocks.nextImage,
    nextBlogger: mocks.nextBlogger,
    rate: mocks.rate,
    removeCurrent: mocks.removeCurrent,
    stop: mocks.stop,
  }
  mocks.api = api
  return { usePriorityRating: () => api }
})

import PersonRatingView from '../PersonRatingView.vue'
import TrashReasonModal from '@/components/inspiration/TrashReasonModal.vue'

const BLOGGER = {
  blogger_id: 1,
  name: '小葵',
  platform: 'douyin',
  avatar_path: null,
  image_count: 10,
  rated_count: 2,
  unrated_count: 8,
  rating_average: null,
  coverage: 0.2,
}

const IMAGE = {
  inspiration_id: 'insp-1',
  file_path: 'images/2026-11/a.jpg',
  thumbnail_path: 'thumbnails/2026-11/a.jpg',
  media_type: 'image',
  confidence: 0,
  created_at: '2026-11-10T12:00:00Z',
  quality_status: 'pending',
  rating: 0,
  is_favorite: false,
}

type MockApi = {
  currentBlogger: { value: unknown }
  current: { value: unknown }
  finished: { value: boolean }
  ratedCount: { value: number }
  error: { value: string | null }
  queue: { value: unknown[] }
}

function api(): MockApi {
  return mocks.api as MockApi
}

function mountView() {
  return mount(PersonRatingView, { global: { plugins: [ArcoVue] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  api().currentBlogger.value = { ...BLOGGER }
  api().current.value = { ...IMAGE }
  api().finished.value = false
  api().ratedCount.value = 0
  api().error.value = null
  api().queue.value = [{ ...BLOGGER }]
})

describe('PersonRatingView', () => {
  it('展示博主资料、无样本平均分显示 --、图片统计与覆盖率', async () => {
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('人物优先评分')
    expect(text).toContain('小葵')
    expect(text).toContain('抖音')
    expect(text).toContain('--') // 无评分样本
    expect(text).toContain('10') // 图片素材
    expect(text).toContain('2') // 已评
    expect(text).toContain('8') // 未评
    expect(text).toContain('20%') // 覆盖率
    expect(mocks.loadQueue).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.rate-image').exists()).toBe(true)
    expect(wrapper.find('.rate-image').attributes('src')).toContain('images/2026-11/a.jpg')
    wrapper.unmount()
  })

  it('数字键 1-5 评分，Esc 返回人物管理', async () => {
    const wrapper = mountView()
    await flushPromises()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '4' }))
    await flushPromises()
    expect(mocks.rate).toHaveBeenCalledWith(4)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(mocks.routerPush).toHaveBeenCalledWith('/persons')
    wrapper.unmount()
  })

  it('删除必须先选原因，确认后调用 removeCurrent', async () => {
    const wrapper = mountView()
    await flushPromises()

    const deleteButton = wrapper.findAll('button').find((b) => b.text() === '删除')
    expect(deleteButton).toBeTruthy()
    await deleteButton!.trigger('click')
    await flushPromises()

    const modal = wrapper.findComponent(TrashReasonModal)
    expect(modal.props('visible')).toBe(true)
    expect(mocks.removeCurrent).not.toHaveBeenCalled()

    modal.vm.$emit('confirm', '不喜欢')
    await flushPromises()
    expect(mocks.removeCurrent).toHaveBeenCalledWith('insp-1', '不喜欢')
    wrapper.unmount()
  })

  it('「换一位博主」按钮接到 nextBlogger', async () => {
    const wrapper = mountView()
    await flushPromises()

    const nextBlogger = wrapper.findAll('button').find((b) => b.text() === '换一位博主')
    expect(nextBlogger).toBeTruthy()
    await nextBlogger!.trigger('click')
    expect(mocks.nextBlogger).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('队列为空时展示空态文案，不渲染大图', async () => {
    api().currentBlogger.value = null
    api().current.value = null
    api().queue.value = []
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.rate-image').exists()).toBe(false)
    expect(wrapper.text()).toContain('还没有需要评分的博主')
    wrapper.unmount()
  })
})
