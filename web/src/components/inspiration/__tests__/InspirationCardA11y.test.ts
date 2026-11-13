/** 素材卡键盘/读屏语义：卡片可聚焦、回车打开、选择模式为 checkbox。 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({ openInspiration: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('@/utils/openInspiration', () => ({ openInspiration: mocks.openInspiration }))

import InspirationCard from '../InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

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

describe('InspirationCard 无障碍', () => {
  it('可聚焦、有读屏标签，回车打开详情', async () => {
    mocks.openInspiration.mockClear()
    const wrapper = mountCard()
    const card = wrapper.find('.card')

    expect(card.attributes('role')).toBe('button')
    expect(card.attributes('tabindex')).toBe('0')
    expect(card.attributes('aria-label')).toContain('打开图片素材')
    expect(card.attributes('aria-label')).toContain('小葵')

    await card.trigger('keydown', { key: 'Enter' })
    expect(mocks.openInspiration).toHaveBeenCalledTimes(1)
  })

  it('批量选择模式暴露 checkbox 语义并响应空格', async () => {
    const wrapper = mountCard({ selectable: true, selected: true })
    const card = wrapper.find('.card')

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
})
