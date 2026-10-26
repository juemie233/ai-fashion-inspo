/**
 * 参考图选择器：浏览式多选（搜索 / 来源筛选 / 顺序编号）。
 *
 * 为什么值得测：图生图的参考图顺序会影响生成（第 1 张是主参考），而用户是**点选**出来的，
 * 所以「点的顺序 = 提交的顺序」必须锁死；搜索/来源筛选则决定「能不能翻到想要的那张」。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue, { Message } from '@arco-design/web-vue'

import ReferencePickerModal from '../ReferencePickerModal.vue'

const mocks = vi.hoisted(() => ({
  search: vi.fn(),
}))

vi.mock('@/api/search', () => ({ searchInspirations: mocks.search }))
vi.mock('@/api/inspirations', () => ({
  getFileUrl: (path: string) => `/files/${path}`,
  fetchInspirations: vi.fn(),
}))

/** 弹窗内容用透传替身渲染：真 Modal 会 teleport 到 body，交互断言不方便 */
const ModalStub = {
  name: 'AModalStub',
  props: ['visible'],
  template: '<div class="modal-stub"><slot /></div>',
}

function item(id: string, source = 'douyin') {
  return {
    id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    source_type: source,
  }
}

function mountPicker(props: Record<string, unknown> = {}) {
  return mount(ReferencePickerModal, {
    props: { visible: false, selectedIds: [], max: 10, ...props },
    global: {
      plugins: [ArcoVue],
      stubs: { 'a-modal': ModalStub },
    },
  })
}

describe('ReferencePickerModal 浏览与多选', () => {
  beforeEach(() => {
    mocks.search.mockReset()
    mocks.search.mockResolvedValue({ items: [item('a'), item('b')], total: 2 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('打开即按「只看图片」拉第一页', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(mocks.search).toHaveBeenCalledWith({
      keyword: undefined,
      source_type: undefined,
      media_type: 'image',
      page: 1,
      size: 40,
    })
    expect(wrapper.findAll('.pick-item')).toHaveLength(2)
  })

  it('关键词搜索重新从第 1 页查（素材多时靠它翻到想要的那张）', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    // 直接走组件的 search 事件（点搜索图标时 Arco 走的就是这条路径，传的是输入框的值）
    wrapper.findComponent({ name: 'InputSearch' }).vm.$emit('search', '针织裙')
    await flushPromises()

    expect(mocks.search).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '针织裙', page: 1 }),
    )
  })

  it('点的顺序就是提交的顺序（第 1 张是主参考）', async () => {
    mocks.search.mockResolvedValue({ items: [item('a'), item('b'), item('c')], total: 3 })
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    const cells = wrapper.findAll('.pick-item')
    await cells[2].trigger('click')
    await cells[0].trigger('click')
    await flushPromises()

    // 角标显示的顺序 = 点击顺序
    expect(wrapper.findAll('.pick-mark').map((n) => n.text())).toEqual(['2', '1'])

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('确定'))!
      .trigger('click')
    expect(wrapper.emitted('confirm')?.[0]).toEqual([['c', 'a']])
  })

  it('再点一次取消选择；超过上限时忽略并提示', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const wrapper = mountPicker({ max: 1 })
    await wrapper.setProps({ visible: true })
    await flushPromises()

    const cells = wrapper.findAll('.pick-item')
    await cells[0].trigger('click')
    await cells[1].trigger('click') // 超上限：忽略 + 警告
    await flushPromises()

    expect(warn).toHaveBeenCalled()
    expect(wrapper.findAll('.pick-mark')).toHaveLength(1)

    await cells[0].trigger('click') // 再点自己 = 取消
    await flushPromises()
    expect(wrapper.findAll('.pick-mark')).toHaveLength(0)
  })

  it('已选条带可用 × 移除，编号跟着重排', async () => {
    mocks.search.mockResolvedValue({ items: [item('a'), item('b')], total: 2 })
    const wrapper = mountPicker({ selectedIds: ['a', 'b'] })
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(wrapper.findAll('.pick-chip')).toHaveLength(2)
    expect(wrapper.findAll('.pick-chip-del')[0].attributes('title')).toBe('移除')
    await wrapper.findAll('.pick-chip-del')[0].trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.pick-chip')).toHaveLength(1)
    expect(wrapper.find('.pick-mark').text()).toBe('1')
  })

  it('切「来源」只重拉列表，不会把来源值当成关键词（回归：Arco select 的 change 第一参是选中值）', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()
    mocks.search.mockClear()

    const select = wrapper.findComponent({ name: 'Select' })
    select.vm.$emit('update:modelValue', 'douyin')
    select.vm.$emit('change', 'douyin')
    await flushPromises()

    expect(mocks.search).toHaveBeenLastCalledWith({
      keyword: undefined, // 关键：来源值不能写进 keyword，否则查询变成「关键词=douyin」
      source_type: 'douyin',
      media_type: 'image',
      page: 1,
      size: 40,
    })
  })

  it('重新打开会清掉上次的搜索条件（关键词与来源都不跨次残留）', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    const input = wrapper.findComponent({ name: 'InputSearch' })
    input.vm.$emit('search', '针织裙')
    await flushPromises()
    expect(mocks.search).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: '针织裙' }))

    await wrapper.setProps({ visible: false })
    mocks.search.mockClear()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(mocks.search).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: undefined, source_type: undefined }),
    )
  })
})
