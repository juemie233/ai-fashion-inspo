/** 姿势选择器：打开加载、卡片渲染、选中回抛、空状态。
 *
 * 数据源是后端 `/api/pose-gallery`（已代理源站并缓存），这里只 mock API 层，
 * 不关心 axios/网络。弹窗用 stub 渲染（真实 Arco modal 走 teleport，jsdom 里不稳）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import PosePickerModal from '../PosePickerModal.vue'
import type { PoseGalleryMeta, PoseItem } from '@/api/poseGallery'

const mocks = vi.hoisted(() => ({
  meta: vi.fn(),
  poses: vi.fn(),
}))

vi.mock('@/api/poseGallery', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/poseGallery')>()
  return { ...actual, fetchPoseGalleryMeta: mocks.meta, fetchPoses: mocks.poses }
})

const META: PoseGalleryMeta = {
  total: 2,
  systems: [{ name: '全身单人・标准系', count: 1 }],
  categories: [{ name: '站姿', count: 1 }],
  angles: [{ name: '正面', count: 1 }],
  note: '图片来自 ai-pose-gallery-zh（个人本地使用）',
  source_url: 'https://ai-pose-gallery.anhao.app',
}

const POSES: PoseItem[] = [
  {
    id: 'full_std_P001_front',
    name: 'S形站姿',
    system: '全身单人・标准系',
    category: '站姿',
    angle: '正面',
    pose_no: 'P001',
    thumb_url: 'https://example.com/thumbs/a.jpg',
    image_url: 'https://example.com/img/a.png',
  },
  {
    id: 'upper_P003_low',
    name: '半身坐姿',
    system: '上半身单人',
    category: '坐姿',
    angle: '俯视',
    pose_no: 'P003',
    thumb_url: 'https://example.com/thumbs/b.jpg',
    image_url: 'https://example.com/img/b.png',
  },
]

const ModalStub = {
  name: 'AModalStub',
  props: ['visible', 'title', 'width', 'footer'],
  template: '<div class="modal-stub"><slot /></div>',
}

function mountPicker() {
  return mount(PosePickerModal, {
    props: { visible: false },
    global: { plugins: [ArcoVue], stubs: { 'a-modal': ModalStub } },
    attachTo: document.body,
  })
}

describe('PosePickerModal', () => {
  beforeEach(() => {
    mocks.meta.mockReset()
    mocks.poses.mockReset()
    mocks.meta.mockResolvedValue(META)
    mocks.poses.mockResolvedValue({
      items: POSES,
      total: 2,
      page: 1,
      size: 40,
      note: META.note,
    })
    document.body.innerHTML = ''
  })

  it('打开时拉元信息与第一页，渲染姿势卡片与来源说明', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(mocks.meta).toHaveBeenCalledTimes(1)
    expect(mocks.poses).toHaveBeenCalledWith({
      q: undefined,
      system: undefined,
      category: undefined,
      angle: undefined,
      page: 1,
      size: 40,
    })
    expect(wrapper.findAll('.pose-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('共 2 个姿势')
    expect(wrapper.text()).toContain('ai-pose-gallery-zh')
    expect(wrapper.find('.pose-card img').attributes('src')).toBe(POSES[0].thumb_url)
    wrapper.unmount()
  })

  it('点姿势卡片：回抛选中项并关闭弹窗', async () => {
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    await wrapper.findAll('.pose-card')[1].trigger('click')

    expect(wrapper.emitted('select')?.[0]).toEqual([POSES[1]])
    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
    wrapper.unmount()
  })

  it('空结果给出可执行提示（不显示空白弹窗）', async () => {
    mocks.poses.mockResolvedValue({ items: [], total: 0, page: 1, size: 40, note: META.note })
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(wrapper.findAll('.pose-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('没有匹配的姿势')
    wrapper.unmount()
  })

  it('加载失败时显示错误文案', async () => {
    mocks.poses.mockRejectedValue(new Error('姿势库暂时不可用'))
    const wrapper = mountPicker()
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(wrapper.text()).toContain('姿势库暂时不可用')
    wrapper.unmount()
  })
})
