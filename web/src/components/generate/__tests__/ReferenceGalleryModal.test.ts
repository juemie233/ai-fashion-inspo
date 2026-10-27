/**
 * 已选参考图的大图浏览：翻页、键盘、越界夹紧、空态。
 *
 * 这个弹窗的关键不是「能显示图」，而是**别把参考顺序搞错**：第 1 张是主参考，
 * 编号与提示必须与出图时的顺序一致。
 */

import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import ReferenceGalleryModal from '../ReferenceGalleryModal.vue'

// a-modal 会把内容 teleport 到 body，wrapper.text() 读不到；用 stub 就地渲染
const ModalStub = {
  name: 'AModalStub',
  props: ['visible', 'title', 'width', 'footer'],
  template: '<div class="modal-stub"><slot /></div>',
}

const ITEMS = [
  { id: 'ref-a', file_path: 'images/a.jpg', thumbnail_path: 'thumbnails/a.jpg' },
  { id: 'ref-b', file_path: 'images/b.jpg', thumbnail_path: null },
  { id: 'ref-c', file_path: 'images/c.jpg', thumbnail_path: 'thumbnails/c.jpg' },
]

function mountModal(props: Record<string, unknown> = {}) {
  return mount(ReferenceGalleryModal, {
    props: { visible: true, items: ITEMS, startIndex: 0, ...props },
    global: { plugins: [ArcoVue], stubs: { 'a-modal': ModalStub } },
    attachTo: document.body,
  })
}

afterEach(() => {
  document.body.innerHTML = ''
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
})

describe('ReferenceGalleryModal', () => {
  it('按 startIndex 定位，并显示参考顺序与主参考提示', () => {
    const wrapper = mountModal({ startIndex: 1 })
    expect(wrapper.text()).toContain('参考 2 / 3')
    expect(wrapper.text()).toContain('第 1 张是主参考')
    // 大图用原图（看版型与面料细节）
    expect(wrapper.find('.rg-img').attributes('src')).toContain('images/b.jpg')
    wrapper.unmount()
  })

  it('← → 按钮翻页，边界禁用', async () => {
    const wrapper = mountModal()
    const buttons = wrapper.findAll('.rg-footer button')
    expect(buttons[0].attributes('disabled')).toBeDefined() // 第 1 张时「上一张」禁用

    await buttons[1].trigger('click')
    expect(wrapper.text()).toContain('参考 2 / 3')

    await wrapper.findAll('.rg-footer button')[0].trigger('click')
    expect(wrapper.text()).toContain('参考 1 / 3')
    wrapper.unmount()
  })

  it('键盘 ← → 翻页', async () => {
    const wrapper = mountModal()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('参考 2 / 3')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('参考 1 / 3')
    wrapper.unmount()
  })

  it('startIndex 超出范围时夹紧（参考图被删也不会白屏）', () => {
    const wrapper = mountModal({ startIndex: 99 })
    expect(wrapper.text()).toContain('参考 3 / 3')
    wrapper.unmount()
  })

  it('素材已不在库里：给明确空态而不是破图', () => {
    const wrapper = mountModal({ items: [] })
    expect(wrapper.find('.rg-img').exists()).toBe(false)
    expect(wrapper.text()).toContain('已经不在素材库里')
    wrapper.unmount()
  })
})
