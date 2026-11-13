/** 缩略图卡片：状态遮罩、序号、选中、悬停预览开关与插槽。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import HoverImagePreview from '../HoverImagePreview.vue'
import ThumbCard from '../ThumbCard.vue'

function mountCard(props: Record<string, unknown> = {}) {
  return mount(ThumbCard, {
    props: { src: '/a.jpg', alt: '图', ...props },
    global: { plugins: [ArcoVue] },
  })
}

describe('ThumbCard', () => {
  it('渲染序号、选中框与失败遮罩（title 带原因）', () => {
    const wrapper = mountCard({
      index: 2,
      selected: true,
      status: 'failed',
      statusText: '网络错误',
    })

    expect(wrapper.find('.thumb-index').text()).toBe('2')
    expect(wrapper.find('.thumb-selected-mask').exists()).toBe(true)
    const mask = wrapper.find('.thumb-mask.failed')
    expect(mask.exists()).toBe(true)
    expect(mask.attributes('title')).toBe('网络错误')
    wrapper.unmount()
  })

  it('done / duplicate 状态使用对应遮罩', () => {
    const done = mountCard({ status: 'done' })
    expect(done.find('.thumb-mask.done').text()).toBe('✓')
    done.unmount()

    const dup = mountCard({ status: 'duplicate', statusText: '重复文件' })
    const dupMask = dup.find('.thumb-mask.dup')
    expect(dupMask.exists()).toBe(true)
    expect(dupMask.attributes('title')).toBe('重复文件')
    dup.unmount()
  })

  it('hoverSrc 存在时启用悬停大图，未传时不渲染', () => {
    const withHover = mountCard({ hoverSrc: '/big.jpg' })
    expect(withHover.findComponent(HoverImagePreview).exists()).toBe(true)
    expect(withHover.findComponent(HoverImagePreview).props('largeSrc')).toBe('/big.jpg')
    withHover.unmount()

    const withoutHover = mountCard()
    expect(withoutHover.findComponent(HoverImagePreview).exists()).toBe(false)
    withoutHover.unmount()
  })

  it('extra / footer 插槽注入到媒体区与卡片下方', () => {
    const wrapper = mount(ThumbCard, {
      props: { src: '/a.jpg' },
      slots: {
        extra: '<span class="extra-slot">操作</span>',
        footer: '<span class="footer-slot">来源</span>',
      },
      global: { plugins: [ArcoVue] },
    })

    expect(wrapper.find('.thumb-media-area .extra-slot').exists()).toBe(true)
    expect(wrapper.find('.footer-slot').exists()).toBe(true)
    wrapper.unmount()
  })
})
