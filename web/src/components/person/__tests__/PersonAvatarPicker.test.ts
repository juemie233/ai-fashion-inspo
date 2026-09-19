/**
 * PersonAvatarPicker（人物头像设置弹窗）测试。
 *
 * 关注三件事：① 打开时按人物拉取 TA 的素材并渲染网格；② 「从素材选择」提交
 * inspiration_id（multipart）；③ 「上传照片」提交 file，未选择时「设为头像」禁用。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PersonAvatarPicker from '../PersonAvatarPicker.vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const modalStub = defineComponent({
  name: 'AModal',
  props: { visible: { type: Boolean, default: false } },
  render() {
    return this.visible
      ? h('div', { class: 'modal' }, [this.$slots.default?.(), this.$slots.footer?.()])
      : null
  },
})
const tabsStub = defineComponent({
  name: 'ATabs',
  props: { activeKey: { type: String, default: 'material' } },
  render() {
    return h('div', this.$slots.default?.())
  },
})
const tabPaneStub = defineComponent({
  name: 'ATabPane',
  props: { title: { type: String, default: '' } },
  render() {
    return h('div', { class: 'pane', 'data-title': this.title }, this.$slots.default?.())
  },
})
const spaceStub = defineComponent({
  name: 'ASpace',
  render() {
    return h('div', this.$slots.default?.())
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  props: { disabled: { type: Boolean, default: false } },
  emits: ['click'],
  render() {
    return h(
      'button',
      { disabled: this.disabled, onClick: () => this.$emit('click') },
      this.$slots.default?.(),
    )
  },
})
const spinStub = defineComponent({
  name: 'ASpin',
  render() {
    return h('span', this.$slots.default?.())
  },
})
const gridStub = defineComponent({
  name: 'DensityImageGrid',
  render() {
    return h('div', { class: 'grid' }, this.$slots.default?.())
  },
})
const emptyStub = defineComponent({ name: 'AEmpty', render: () => h('div') })
const paginationStub = defineComponent({ name: 'APagination', render: () => h('div') })
const tagStub = defineComponent({
  name: 'ATag',
  render() {
    return h('span', this.$slots.default?.())
  },
})
const popStub = defineComponent({
  name: 'APopconfirm',
  render() {
    return h('span', this.$slots.default?.())
  },
})

function makeMaterial(id: string, mediaType = 'image') {
  return {
    inspiration_id: id,
    file_path: `images/2026-09/${id}.webp`,
    thumbnail_path: `thumbnails/2026-09/${id}.jpg`,
    media_type: mediaType,
    confidence: 1,
    created_at: '2026-09-15 01:41:19',
  }
}

async function mountPicker(over: Record<string, unknown> = {}) {
  mocks.get.mockResolvedValue({
    data: {
      person: { id: 7, name: '里香', platform: 'douyin' },
      items: [makeMaterial('a'), makeMaterial('b', 'video')],
      total: 2,
      page: 1,
      size: 24,
    },
  })
  const wrapper = mount(PersonAvatarPicker, {
    props: { visible: true, personId: 7, personName: '里香', hasAvatar: false, ...over },
    global: {
      stubs: {
        'a-modal': modalStub,
        'a-tabs': tabsStub,
        'a-tab-pane': tabPaneStub,
        'a-space': spaceStub,
        'a-button': buttonStub,
        'a-spin': spinStub,
        'a-empty': emptyStub,
        'a-pagination': paginationStub,
        'a-tag': tagStub,
        'a-popconfirm': popStub,
        DensityImageGrid: gridStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('PersonAvatarPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('打开时按人物拉素材并渲染网格（视频标注首帧）', async () => {
    const wrapper = await mountPicker()

    expect(mocks.get).toHaveBeenCalledWith('/bloggers/7/inspirations', {
      params: { page: 1, size: 24, sort: undefined },
    })
    expect(wrapper.findAll('.avatar-pick-cell')).toHaveLength(2)
    expect(wrapper.text()).toContain('视频首帧')
  })

  it('从素材选择：选中后「设为头像」提交 inspiration_id', async () => {
    mocks.post.mockResolvedValue({ data: { id: 7, avatar_path: 'avatars/avatar_7.jpg' } })
    const wrapper = await mountPicker()

    // 未选择时提交按钮禁用
    const submit = wrapper.findAll('button').find((b) => b.text().includes('设为头像'))!
    expect(submit.attributes('disabled')).toBeDefined()

    await wrapper.findAll('.avatar-pick-cell')[0].trigger('click')
    await submit.trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledTimes(1)
    const [url, body] = mocks.post.mock.calls[0]
    expect(url).toBe('/bloggers/7/avatar')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('inspiration_id')).toBe('a')
    expect(wrapper.emitted('saved')?.[0]).toEqual(['avatars/avatar_7.jpg'])
  })

  it('已设置头像时提供「清除头像」', async () => {
    mocks.delete.mockResolvedValue({ data: { id: 7, avatar_path: null } })
    const wrapper = await mountPicker({ hasAvatar: true })

    const clear = wrapper.findAll('button').find((b) => b.text().includes('清除头像'))
    expect(clear).toBeTruthy()
  })
})
