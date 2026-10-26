/**
 * 出图对比（参考图 ↔ 生成图）：独立对比视图的行为测试。
 *
 * 三条底线：
 * 1. 参考图按 `referenceIds` 的顺序还原（第 1 张是主参考，顺序错了「对比」就没意义）；
 * 2. 候选图可前后翻 / 缩略图直达 / 键盘 ← →，且越界回卷；
 * 3. 叠看模式用透明度滑块压图——与「并排」互不影响。
 *
 * 另外它必须与「点图看大图」的灯箱**互相独立**：本组件只 emit 自己的显隐，不碰候选勾选。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import GenerateCompareModal from '../GenerateCompareModal.vue'

const mocks = vi.hoisted(() => ({
  fetchInspirations: vi.fn(),
}))

vi.mock('@/api/inspirations', () => ({
  fetchInspirations: mocks.fetchInspirations,
  getFileUrl: (path: string) => `/files/${path}`,
}))

const ModalStub = {
  name: 'AModalStub',
  props: ['visible', 'title', 'width', 'footer'],
  template: '<div class="modal-stub"><slot /></div>',
}

const candidates = [
  { url: '/api/generate/x/0.png', seed: 100, filename: '0.png', imported: false },
  { url: '/api/generate/x/1.png', seed: 101, filename: '1.png', imported: true },
  { url: '/api/generate/x/2.png', seed: 102, filename: '2.png', imported: false },
]

function mountModal(props: Record<string, unknown> = {}) {
  return mount(GenerateCompareModal, {
    props: {
      visible: true,
      candidates,
      initialIndex: 0,
      referenceIds: [],
      ...props,
    },
    attachTo: document.body,
    global: { plugins: [ArcoVue], stubs: { 'a-modal': ModalStub } },
  })
}

describe('GenerateCompareModal 对比', () => {
  beforeEach(() => {
    mocks.fetchInspirations.mockReset()
    // 故意倒序返回：组件必须按 referenceIds 的顺序还原，而不是按接口返回顺序
    mocks.fetchInspirations.mockResolvedValue({
      items: [
        { id: 'b', file_path: 'images/b.jpg', thumbnail_path: 'thumbnails/b.jpg' },
        { id: 'a', file_path: 'images/a.jpg', thumbnail_path: 'thumbnails/a.jpg' },
      ],
      total: 2,
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('参考图按 id 顺序还原，并定位到指定候选', async () => {
    const wrapper = mountModal({ initialIndex: 1, referenceIds: ['a', 'b'] })
    await flushPromises()

    expect(mocks.fetchInspirations).toHaveBeenCalledWith({ ids: 'a,b', size: 2 })
    const imgs = wrapper.findAll('.cmp-img')
    expect(imgs[0].attributes('src')).toBe('/files/images/a.jpg')
    expect(imgs[1].attributes('src')).toBe('/api/generate/x/1.png')
    expect(wrapper.text()).toContain('2 / 3')
    expect(wrapper.text()).toContain('seed 101')
  })

  it('下一张/上一张越界回卷，缩略图可直达', async () => {
    const wrapper = mountModal({ initialIndex: 0 })
    await flushPromises()

    const nextBtn = wrapper.findAll('button').find((b) => b.text().includes('下一张'))!
    await nextBtn.trigger('click')
    expect(wrapper.text()).toContain('2 / 3')

    await wrapper.findAll('.cmp-strip-item')[2].trigger('click')
    expect(wrapper.text()).toContain('3 / 3')

    await nextBtn.trigger('click') // 3 → 1，回卷
    expect(wrapper.text()).toContain('1 / 3')
  })

  it('键盘 ← → 切换候选', async () => {
    const wrapper = mountModal({ initialIndex: 0 })
    await flushPromises()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    await flushPromises()
    expect(wrapper.text()).toContain('2 / 3')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
    await flushPromises()
    expect(wrapper.text()).toContain('1 / 3')
  })

  it('切到「叠看」后用透明度滑块压图（并排面板隐藏）', async () => {
    const wrapper = mountModal({ initialIndex: 0, referenceIds: ['a'] })
    await flushPromises()
    expect(wrapper.find('.cmp-side').exists()).toBe(true)

    wrapper.findComponent({ name: 'RadioGroup' }).vm.$emit('update:modelValue', 'overlay')
    await flushPromises()

    expect(wrapper.find('.cmp-side').exists()).toBe(false)
    const top = wrapper.find('.cmp-overlay-top')
    expect(top.exists()).toBe(true)
    expect(top.attributes('style')).toContain('0.55')
  })

  it('没有参考图时给出说明而不是空白（文生图批次也走同一个弹窗）', async () => {
    const wrapper = mountModal({ referenceIds: [] })
    await flushPromises()

    expect(mocks.fetchInspirations).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本批没有参考图')
  })

  it('只 emit 自己的显隐，不触碰候选勾选（与灯箱浏览互不影响）', async () => {
    const wrapper = mountModal()
    await flushPromises()

    expect(Object.keys(wrapper.emitted())).toEqual([])
    wrapper.findComponent({ name: 'AModalStub' }).vm.$emit('cancel')
    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
  })
})
