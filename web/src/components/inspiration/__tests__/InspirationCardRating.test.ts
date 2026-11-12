/** 素材卡片评分交互：点星不打开详情、aria 可读、change 事件上抛。 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  openInspiration: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('@/utils/openInspiration', () => ({ openInspiration: mocks.openInspiration }))

import InspirationCard from '../InspirationCard.vue'

const ITEM = {
  id: 'insp-1',
  source_type: 'douyin',
  file_path: 'images/a.jpg',
  thumbnail_path: 'thumbnails/a.jpg',
  media_type: 'image',
  is_favorite: false,
  rating: 3,
  quality_status: 'approved',
  is_ai_generated: false,
  tags: [],
} as never

function mountCard() {
  return mount(InspirationCard, {
    props: { item: ITEM, showActions: true },
    global: { plugins: [ArcoVue] },
  })
}

describe('InspirationCard 评分', () => {
  it('点星不触发卡片打开详情，且 aria 含当前评分', async () => {
    const wrapper = mountCard()
    const rate = wrapper.find('.card-rate')
    expect(rate.exists()).toBe(true)
    expect(rate.attributes('aria-label')).toContain('当前 3 星')

    await rate.trigger('click')
    expect(mocks.openInspiration).not.toHaveBeenCalled()
  })

  it('评分变化上抛 rate 事件', () => {
    const wrapper = mountCard()
    wrapper.findComponent({ name: 'Rate' }).vm.$emit('change', 4)
    expect(wrapper.emitted('rate')?.[0]?.[0]).toBe(4)
  })
})
