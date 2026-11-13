/** 素材卡片删除按钮：独立于 showActions（人物详情按张移入垃圾桶）。 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('@/utils/openInspiration', () => ({ openInspiration: vi.fn() }))

import InspirationCard from '../InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

function item(overrides: Partial<InspirationOut> = {}): InspirationOut {
  return {
    id: 'insp-1',
    file_path: 'images/a.jpg',
    thumbnail_path: 'thumbnails/a.jpg',
    media_type: 'image',
    is_favorite: false,
    rating: 3,
    quality_status: 'approved',
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

describe('InspirationCard 删除按钮', () => {
  it('showDelete=true 时显示删除小按钮，并可上抛 delete 事件', () => {
    const wrapper = mountCard({ showDelete: true })
    const button = wrapper.find('.card-delete')

    expect(button.exists()).toBe(true)
    expect(button.attributes('title')).toBe('移入垃圾桶')

    wrapper.findComponent({ name: 'Popconfirm' }).vm.$emit('ok')
    expect(wrapper.emitted('delete')).toHaveLength(1)
  })

  it('默认不显示删除按钮（showActions=false 的评分/收藏卡片不受影响）', () => {
    const wrapper = mountCard()
    expect(wrapper.find('.card-delete').exists()).toBe(false)
  })

  it('showActions=true 时仍保留原有删除入口', () => {
    const wrapper = mountCard({ showActions: true })
    expect(wrapper.find('.card-delete').exists()).toBe(true)
  })
})
