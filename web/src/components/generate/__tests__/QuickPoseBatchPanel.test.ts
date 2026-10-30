/** 一键出图（性感系姿势轮播）面板。
 *
 * 重点验证三件事：
 * 1. 打开就按体系拉全姿势，并显示「下一个姿势 + 已用进度」；
 * 2. 点一次用**下一个未用过**的姿势出一批（回抛 run），进度落 localStorage；
 * 3. 「连续 N 次」会等上一轮结束（busy true→false）再自动出下一轮，最后回抛 batch-end。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import QuickPoseBatchPanel from '../QuickPoseBatchPanel.vue'
import type { PoseItem } from '@/api/poseGallery'

const mocks = vi.hoisted(() => ({
  fetchPoses: vi.fn(),
}))

vi.mock('@/api/poseGallery', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/poseGallery')>()
  return { ...actual, fetchPoses: mocks.fetchPoses }
})

function pose(no: number): PoseItem {
  return {
    id: `full_sexy_P00${no}_front`,
    name: `姿势 ${no}`,
    system: '全身单人・性感系',
    category: '站姿',
    angle: '正面',
    pose_no: `P00${no}`,
    thumb_url: `https://example.com/thumbs/${no}.jpg`,
    image_url: `https://example.com/img/${no}.png`,
  }
}

const POSES = [pose(1), pose(2), pose(3)]

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(QuickPoseBatchPanel, {
    props: {
      platformReady: true,
      modelReady: true,
      hasReferences: true,
      busy: false,
      ...props,
    },
    global: { plugins: [ArcoVue] },
  })
}

describe('QuickPoseBatchPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    mocks.fetchPoses.mockReset()
    mocks.fetchPoses.mockResolvedValue({
      items: POSES,
      total: POSES.length,
      page: 1,
      size: 100,
      note: '',
    })
  })

  it('加载姿势并显示下一个姿势与进度', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(mocks.fetchPoses).toHaveBeenCalledWith({
      system: '全身单人・性感系',
      page: 1,
      size: 100,
    })
    expect(wrapper.text()).toContain('姿势 1 · 正面')
    expect(wrapper.text()).toContain('已用 0/3')
    wrapper.unmount()
  })

  it('点一键出图：用下一个姿势出一批，进度落 localStorage', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('一键出图'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()

    const payload = wrapper.emitted('run')?.[0]?.[0] as Record<string, unknown>
    expect(payload.pose).toMatchObject({ id: 'full_sexy_P001_front' })
    expect(payload.count).toBe(2) // 默认每次 2 张
    expect(payload.round).toBe(1)
    expect(payload.rounds).toBe(1)

    // 下一个姿势前进到第 2 个，进度 1/3
    expect(wrapper.text()).toContain('姿势 2 · 正面')
    expect(wrapper.text()).toContain('已用 1/3')
    const saved = JSON.parse(localStorage.getItem('fashion-inspo:quickpose:used') || '{}')
    expect(saved['全身单人・性感系']).toEqual(['full_sexy_P001_front'])
    wrapper.unmount()
  })

  it('连续 2 次：等上一轮 busy 结束再自动出下一轮，最后回抛 batch-end', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    // 连续次数改成 2（第三个数字输入框：每次张数 / 步数 / 连续次数）
    const inputs = wrapper.findAll('.quick-controls input')
    expect(inputs.length).toBe(3)
    const roundInput = inputs[2]
    await roundInput.setValue('2')
    await roundInput.trigger('change')
    await roundInput.trigger('blur')
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('一键出图'))
    await btn!.trigger('click')
    await flushPromises()
    expect(wrapper.emitted('run')?.length).toBe(1)

    // 第 1 轮开始跑 → 跑完
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await vi.waitFor(() => {
      expect(wrapper.emitted('run')?.length).toBe(2)
    })

    // 第 2 轮结束 → 收尾
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await vi.waitFor(() => {
      expect(wrapper.emitted('batch-end')?.[0]?.[0]).toEqual({ rounds: 2, images: 4 })
    })
    wrapper.unmount()
  })

  it('没选参考图时按钮禁用并给原因', async () => {
    const wrapper = mountPanel({ hasReferences: false })
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('一键出图'))
    expect(btn!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('先在上方选 1 张以上参考图')
    wrapper.unmount()
  })

  it('重置进度会把已用姿势清空', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('一键出图'))!
      .trigger('click')
    await flushPromises()
    // 跑完这一轮（busy true→false）队列才收尾，之后「重置进度」才可用
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await flushPromises()
    expect(wrapper.text()).toContain('已用 1/3')

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('重置进度'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已用 0/3')
    const saved = JSON.parse(localStorage.getItem('fashion-inspo:quickpose:used') || '{}')
    expect(saved['全身单人・性感系']).toEqual([])
    wrapper.unmount()
  })
})
