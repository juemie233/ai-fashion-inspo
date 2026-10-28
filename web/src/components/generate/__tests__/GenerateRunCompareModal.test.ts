/** 两批对比弹窗：两列独立选图、默认第一张、缺批次给空态。 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({ fetchRun: vi.fn() }))

vi.mock('@/api/generate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/generate')>()
  return { ...actual, fetchRun: mocks.fetchRun }
})

import GenerateRunCompareModal from '../GenerateRunCompareModal.vue'

const ModalStub = {
  name: 'AModalStub',
  props: ['visible', 'title', 'width', 'footer'],
  template: '<div class="modal-stub"><slot /></div>',
}

function detail(id: number, seeds: number[]) {
  return {
    run: {
      id,
      kind: 'i2i',
      status: 'done',
      prompt: `第 ${id} 批的描述`,
      negative: null,
      params: {},
      model_key: 'qwen_2_1_i2i',
      workflow_name: 'w',
      total: seeds.length,
      done: seeds.length,
      failed: 0,
      error: null,
      created_at: '2026-10-26T12:00:00Z',
      updated_at: '2026-10-26T12:01:00Z',
    },
    items: seeds.map((seed) => ({
      filename: `${seed}_seed-${seed}.png`,
      index: seed,
      seed,
      imported: false,
      url: `/api/c/${id}/${seed}.png`,
    })),
    task: null,
  }
}

function mountModal(runIds: [number, number] | null = [1, 2]) {
  return mount(GenerateRunCompareModal, {
    props: { visible: true, runIds },
    global: { plugins: [ArcoVue], stubs: { 'a-modal': ModalStub } },
    attachTo: document.body,
  })
}

describe('GenerateRunCompareModal', () => {
  beforeEach(() => {
    mocks.fetchRun.mockReset()
    mocks.fetchRun.mockImplementation((id: number) =>
      Promise.resolve(detail(id, id === 1 ? [11, 12] : [21, 22])),
    )
    document.body.innerHTML = ''
  })

  it('两列各拉一次详情，默认选第一张', async () => {
    const wrapper = mountModal()
    await flushPromises()

    expect(mocks.fetchRun).toHaveBeenCalledWith(1)
    expect(mocks.fetchRun).toHaveBeenCalledWith(2)
    expect(wrapper.findAll('.gc-col')).toHaveLength(2)
    expect(wrapper.findAll('.gc-thumb')).toHaveLength(4)

    const images = wrapper.findAll('.gc-stage img')
    expect(images).toHaveLength(2)
    expect(images[0].attributes('src')).toContain('/api/c/1/11.png')
    expect(images[1].attributes('src')).toContain('/api/c/2/21.png')

    wrapper.unmount()
  })

  it('点第二张缩略图切换该列大图（两列互不影响）', async () => {
    const wrapper = mountModal()
    await flushPromises()

    const leftThumbs = wrapper.findAll('.gc-col')[0].findAll('.gc-thumb')
    await leftThumbs[1].trigger('click')

    const images = wrapper.findAll('.gc-stage img')
    expect(images[0].attributes('src')).toContain('/api/c/1/12.png')
    expect(images[1].attributes('src')).toContain('/api/c/2/21.png')

    wrapper.unmount()
  })

  it('某批拉取失败时该列显示空态（另一列照常）', async () => {
    mocks.fetchRun.mockImplementation((id: number) =>
      id === 2 ? Promise.reject(new Error('404')) : Promise.resolve(detail(id, [11])),
    )
    const wrapper = mountModal()
    await flushPromises()

    const cols = wrapper.findAll('.gc-col')
    expect(cols[0].find('.gc-stage img').exists()).toBe(true)
    expect(cols[1].text()).toContain('这批没有候选图')

    wrapper.unmount()
  })

  it('未选满两批时不请求', async () => {
    const wrapper = mountModal(null)
    await flushPromises()
    expect(mocks.fetchRun).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
