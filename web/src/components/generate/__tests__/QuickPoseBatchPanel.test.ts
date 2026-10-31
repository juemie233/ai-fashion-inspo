/** 一键出图（性感系姿势轮播）面板。
 *
 * 重点验证三件事：
 * 1. 打开就按体系拉全姿势，并显示「下一个姿势 + 已用进度」；
 * 2. 点一次用**下一个未用过**的姿势出一批（回抛 run），进度落 localStorage；
 * 3. 「连续 N 次」会等上一轮结束（busy true→false）再自动出下一轮，最后回抛 batch-end。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
      poseMode: 'text',
      busy: false,
      ...props,
    },
    global: { plugins: [ArcoVue] },
  })
}

describe('QuickPoseBatchPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

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
    // 用可控时钟模拟「每轮真实耗时 30 秒」：面板会把 <2 秒结束的轮次判定为启动失败，
    // 直接瞬间切换 busy 会触发秒失败保护（那条路径另有专门用例）。
    let fakeNow = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => fakeNow)

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

    // 第 1 轮开始跑 → 30 秒后跑完
    fakeNow += 30_000
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await new Promise((resolve) => setTimeout(resolve, 700))
    await flushPromises()
    expect(wrapper.emitted('run')?.length).toBe(2)

    // 第 2 轮结束 → 收尾
    fakeNow += 30_000
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await flushPromises()
    expect(wrapper.emitted('batch-end')?.[0]?.[0]).toEqual({ rounds: 2, images: 4 })
    wrapper.unmount()
  })

  it('一轮秒失败（<2 秒）：停止队列并把姿势回滚成未使用', async () => {
    let fakeNow = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => fakeNow)

    const wrapper = mountPanel()
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('一键出图'))!
      .trigger('click')
    await flushPromises()
    expect(wrapper.emitted('run')?.length).toBe(1)
    expect(wrapper.text()).toContain('已用 1/3')

    // 提交失败/被取消：busy 很快 true→false（<2 秒）
    fakeNow += 500
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await flushPromises()

    expect(wrapper.emitted('batch-end')?.[0]?.[0]).toEqual({ rounds: 0, images: 0 })
    expect(wrapper.text()).toContain('已用 0/3') // 姿势已回滚
    // 队列已停：后续 busy 变化不会再出下一轮
    fakeNow += 30_000
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await flushPromises()
    expect(wrapper.emitted('run')?.length).toBe(1)
    wrapper.unmount()
  })

  it('没选参考图时按钮禁用并给原因', async () => {
    const wrapper = mountPanel({ hasReferences: false, poseMode: 'lineart' })
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text().includes('一键出图'))
    expect(btn!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('线稿参考方式需要先在上方选 1 张以上主体参考图')
    wrapper.unmount()
  })

  it('重置进度会把已用姿势清空', async () => {
    let fakeNow = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => fakeNow)

    const wrapper = mountPanel()
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('一键出图'))!
      .trigger('click')
    await flushPromises()
    // 跑完这一轮（真实耗时 30 秒）队列才收尾，之后「重置进度」才可用
    fakeNow += 30_000
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

  it('「设为剩余全部」把连续次数设成剩余姿势数（不写死上限）', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const fillAll = wrapper.findAll('button').find((b) => b.text().includes('设为剩余全部'))
    expect(fillAll).toBeTruthy()
    expect(fillAll!.text()).toContain('3') // 3 个姿势都没出过
    await fillAll!.trigger('click')
    await flushPromises()

    const mainBtn = wrapper.findAll('button').find((b) => b.text().includes('一键出图'))
    expect(mainBtn!.text()).toContain('连续 3 次，共 6 张')
    // 连续 3 次给出耗时估算（提示里「分钟/小时」二选一）
    expect(wrapper.text()).toMatch(/约需 [\d.]+ (分钟|小时)/)
    wrapper.unmount()
  })

  it('出完一个姿势后连续上限随之下降，超出的次数自动夹回', async () => {
    let fakeNow = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => fakeNow)

    const wrapper = mountPanel()
    await flushPromises()

    // 先设连续 3 次（<= 剩余 3），再出 1 轮后点「停止连续出图」
    const roundsInput = wrapper.findAll('.quick-controls input')[2]
    await roundsInput.setValue('3')
    await roundsInput.trigger('change')
    await roundsInput.trigger('blur')
    await flushPromises()

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('一键出图'))!
      .trigger('click')
    await flushPromises()
    // 这一轮真实耗时 30 秒（否则会被秒失败保护当成启动失败）
    fakeNow += 30_000
    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false })
    await flushPromises()

    // 第 1 轮已过：停止队列，此时剩余 2 个姿势 → 连续次数被夹回 2
    const stop = wrapper.findAll('button').find((b) => b.text().includes('停止连续出图'))
    expect(stop).toBeTruthy()
    await stop!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('设为剩余全部（2）')
    expect((wrapper.findAll('.quick-controls input')[2].element as HTMLInputElement).value).toBe(
      '2',
    )
    wrapper.unmount()
  })
})
