/** 悬停大图预览：延迟显示、移出隐藏、卸载清理计时器；src 模式按内容宽度。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'

import HoverImagePreview from '../HoverImagePreview.vue'

function layer(): Element | null {
  return document.body.querySelector('.hover-preview-layer')
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('HoverImagePreview', () => {
  it('停留达到 delay 才显示，移出立即清除', async () => {
    const wrapper = mount(HoverImagePreview, {
      props: { largeSrc: '/big.jpg' },
      slots: { default: '<span class="thumb">缩略图</span>' },
    })
    const trigger = wrapper.find('.hover-preview-trigger')

    await trigger.trigger('mouseenter')
    vi.advanceTimersByTime(249)
    await nextTick()
    expect(layer()).toBeNull()

    vi.advanceTimersByTime(1)
    await nextTick()
    expect(layer()).not.toBeNull()

    await trigger.trigger('mouseleave')
    await nextTick()
    expect(layer()).toBeNull()

    wrapper.unmount()
  })

  it('卸载时清理未触发的计时器，不在 body 留下浮层', async () => {
    const wrapper = mount(HoverImagePreview, {
      props: { largeSrc: '/big.jpg' },
      slots: { default: '<span>缩略图</span>' },
    })

    await wrapper.find('.hover-preview-trigger').trigger('mouseenter')
    wrapper.unmount()
    vi.advanceTimersByTime(1000)
    await nextTick()

    expect(layer()).toBeNull()
  })

  it('src 模式直接渲染缩略图，并可切换为按内容宽度', () => {
    const wrapper = mount(HoverImagePreview, {
      props: {
        src: '/small.jpg',
        largeSrc: '/big.jpg',
        alt: '候选图',
        imgClass: 'gh-thumb',
        inline: true,
      },
    })

    const img = wrapper.find('img')
    expect(img.attributes('src')).toBe('/small.jpg')
    expect(img.attributes('alt')).toBe('候选图')
    expect(img.classes()).toContain('gh-thumb')
    expect(wrapper.find('.hover-preview-trigger').classes()).toContain('is-inline')

    wrapper.unmount()
  })
})
