/** 素材卡片展示收藏时间（收藏中心需求 C：卡片有 favorited_at 时显示「收藏于」）。 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), resolve: () => ({ href: '/inspirations/insp-1' }) }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/utils/openInspiration', () => ({ openInspiration: vi.fn() }))

import InspirationCard from '../InspirationCard.vue'
import { formatDateTime } from '@/utils/format'
import type { InspirationOut } from '@/api/inspirations'

const FAVORITED_AT = '2026-11-18T20:15:00'

function item(over: Partial<InspirationOut> = {}): InspirationOut {
  return {
    id: 'insp-1',
    file_path: 'images/a.jpg',
    thumbnail_path: 'thumbnails/a.jpg',
    media_type: 'image',
    is_favorite: true,
    source_type: 'douyin',
    source_author: '小葵',
    created_at: FAVORITED_AT,
    favorited_at: FAVORITED_AT,
    tags: [],
    ...over,
  }
}

function mountCard(over: Partial<InspirationOut> = {}) {
  return mount(InspirationCard, {
    props: { item: item(over) },
    global: {
      plugins: [ArcoVue],
      stubs: { HoverImagePreview: true },
    },
  })
}

describe('InspirationCard 收藏时间', () => {
  it('有 favorited_at 时在说明区显示「收藏于 …」', () => {
    const wrapper = mountCard()
    expect(wrapper.text()).toContain('收藏于')
    expect(wrapper.text()).toContain(formatDateTime(FAVORITED_AT))
  })

  it('没有 favorited_at 时不显示收藏时间', () => {
    const wrapper = mountCard({ favorited_at: null })
    expect(wrapper.text()).not.toContain('收藏于')
  })
})
