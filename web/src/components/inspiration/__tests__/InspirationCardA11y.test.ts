/** 素材卡键盘/读屏语义：卡片可聚焦、回车打开、选择模式为 checkbox。 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  openInspiration: vi.fn(),
  openInNewTab: vi.fn(() => true),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), resolve: () => ({ href: '/inspirations/insp-1' }) }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('@/utils/openInspiration', () => ({ openInspiration: mocks.openInspiration }))
vi.mock('@/utils/openInNewTab', () => ({ openInNewTab: mocks.openInNewTab }))

import InspirationCard from '../InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

/** 可控 IntersectionObserver 桩：捕获实例并手动触发进入视口 */
class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = []
  callback: IntersectionObserverCallback
  observed: Element[] = []
  disconnect = vi.fn()

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    MockIntersectionObserver.instances.push(this)
  }

  observe(el: Element): void {
    this.observed.push(el)
  }

  unobserve(): void {}

  takeRecords(): IntersectionObserverEntry[] {
    return []
  }

  trigger(isIntersecting: boolean): void {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
}

function resetNewTabMock(): void {
  mocks.openInspiration.mockClear()
  mocks.openInNewTab.mockClear()
  mocks.openInNewTab.mockReturnValue(true)
  MockIntersectionObserver.instances = []
}

function item(overrides: Partial<InspirationOut> = {}): InspirationOut {
  return {
    id: 'insp-1',
    file_path: 'images/a.jpg',
    thumbnail_path: 'thumbnails/a.jpg',
    media_type: 'image',
    source_author: '小葵',
    is_favorite: false,
    tags: [],
    ...overrides,
  } as InspirationOut
}

function mountCard(props: Record<string, unknown> = {}) {
  return mount(InspirationCard, {
    props: { item: item(), showRating: false, ...props },
    global: { plugins: [ArcoVue] },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('InspirationCard 无障碍', () => {
  it('可聚焦、有读屏标签，回车打开详情', async () => {
    mocks.openInspiration.mockClear()
    const wrapper = mountCard()
    const card = wrapper.find('.card-hit')

    expect(card.attributes('role')).toBeUndefined()
    expect(card.attributes('href')).toContain('/inspirations/insp-1')
    expect(card.attributes('aria-label')).toContain('打开图片素材')
    expect(card.attributes('aria-label')).toContain('小葵')

    await card.trigger('keydown', { key: 'Enter' })
    expect(mocks.openInspiration).toHaveBeenCalledTimes(1)
  })

  it('批量选择模式暴露 checkbox 语义并响应空格', async () => {
    const wrapper = mountCard({ selectable: true, selected: true })
    const card = wrapper.find('.card-hit')

    expect(card.attributes('role')).toBe('checkbox')
    expect(card.attributes('aria-checked')).toBe('true')
    expect(card.attributes('aria-label')).toContain('选择图片素材')

    await card.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('toggleSelect')).toHaveLength(1)
  })

  it('嵌套按钮的回车不冒泡触发打开详情', async () => {
    mocks.openInspiration.mockClear()
    const wrapper = mountCard({ showActions: true })
    const deleteButton = wrapper.find('.card-delete')

    await deleteButton.trigger('keydown', { key: 'Enter' })
    expect(mocks.openInspiration).not.toHaveBeenCalled()
  })

  it('普通点击走全局打开偏好；Ctrl/⌘ 点击整卡强制新标签', async () => {
    resetNewTabMock()
    const wrapper = mountCard()

    await wrapper.find('.card').trigger('click')
    expect(mocks.openInspiration).toHaveBeenCalledTimes(1)
    expect(mocks.openInNewTab).not.toHaveBeenCalled()

    await wrapper.find('.card').trigger('click', { ctrlKey: true })
    expect(mocks.openInNewTab).toHaveBeenCalledWith('/inspirations/insp-1')
    await wrapper.find('.card').trigger('click', { metaKey: true })
    expect(mocks.openInNewTab).toHaveBeenCalledTimes(2)
    // 修饰键不再走「当前页/偏好」那条路
    expect(mocks.openInspiration).toHaveBeenCalledTimes(1)

    // 真实 <a> 的 Ctrl+点击同样强制新标签（不额外导航当前页）
    await wrapper.find('.card-hit').trigger('click', { ctrlKey: true })
    expect(mocks.openInNewTab).toHaveBeenCalledTimes(3)
    expect(mocks.openInspiration).toHaveBeenCalledTimes(1)
  })

  it('鼠标中键（auxclick button=1）强制新标签；选择模式只勾选不打开', async () => {
    resetNewTabMock()
    const normal = mountCard()
    await normal.find('.card').trigger('auxclick', { button: 1 })
    expect(mocks.openInNewTab).toHaveBeenCalledWith('/inspirations/insp-1')
    expect(mocks.openInspiration).not.toHaveBeenCalled()

    const selecting = mountCard({ selectable: true })
    await selecting.find('.card').trigger('click', { ctrlKey: true })
    await selecting.find('.card').trigger('auxclick', { button: 1 })
    expect(selecting.emitted('toggleSelect')).toHaveLength(2)
    expect(mocks.openInNewTab).toHaveBeenCalledTimes(1) // 只有普通卡片那次
    expect(mocks.openInspiration).not.toHaveBeenCalled()
  })

  it('视频无缩略图：进入视口前不设 src/preload，进入后再加载；卸载断开 observer', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    resetNewTabMock()
    const videoItem = item({ media_type: 'video', thumbnail_path: null })
    const wrapper = mountCard({ item: videoItem })

    const video = wrapper.find('video')
    expect(video.exists()).toBe(true)
    expect(video.attributes('src')).toBeUndefined()
    expect(video.attributes('preload')).toBe('none')
    expect(MockIntersectionObserver.instances).toHaveLength(1)
    expect(MockIntersectionObserver.instances[0].observed).toContain(video.element)

    const observer = MockIntersectionObserver.instances[0]
    observer.trigger(false)
    await wrapper.vm.$nextTick()
    expect(video.attributes('src')).toBeUndefined()

    observer.trigger(true)
    await wrapper.vm.$nextTick()
    expect(video.attributes('src')).toContain('/api/files/images/a.jpg')
    expect(video.attributes('preload')).toBe('metadata')
    expect(observer.disconnect).toHaveBeenCalled()

    // 未进入视口就卸载：同样要断开，避免 observer 泄漏
    MockIntersectionObserver.instances = []
    const lazyWrapper = mountCard({ item: videoItem })
    const lazyObserver = MockIntersectionObserver.instances[0]
    lazyWrapper.unmount()
    expect(lazyObserver.disconnect).toHaveBeenCalled()
  })
})
