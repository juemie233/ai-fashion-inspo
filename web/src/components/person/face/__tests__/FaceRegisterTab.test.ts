/** 批量注册 tab：计划加载、采用单条、跳过。 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import FaceRegisterTab from '../FaceRegisterTab.vue'

const mocks = vi.hoisted(() => ({
  fetchFaceRegisterPlan: vi.fn(),
  registerFaceBatch: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/api/faceScan', () => ({
  fetchFaceRegisterPlan: mocks.fetchFaceRegisterPlan,
  registerFaceBatch: mocks.registerFaceBatch,
}))
vi.mock('@/utils/apiError', () => ({ getApiErrorMessage: (_e: unknown, f: string) => f }))
vi.mock('@arco-design/web-vue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@arco-design/web-vue')>()
  return {
    ...actual,
    Message: { success: mocks.success, error: mocks.error, info: vi.fn() },
  }
})

const PLAN = {
  items: [
    {
      person_type: 'blogger' as const,
      person_id: 192,
      name: '示例博主',
      avatar_path: null,
      inspiration_count: 342,
      registered: false,
      skip_reason: null,
      clusters: [
        {
          size: 18,
          avg_det_score: 0.71,
          suggested_inspiration_ids: ['a', 'b', 'c'],
          cover_detection_ids: [1, 2, 3],
          candidates: [
            {
              detection_id: 1,
              inspiration_id: 'a',
              det_score: 0.7,
              thumbnail_path: 'thumbs/a.jpg',
            },
          ],
        },
      ],
    },
  ],
  summary: {
    total: 484,
    with_candidates: 1,
    no_candidates: 483,
    min_cluster_size: 3,
    min_det_score: 0.5,
  },
}

function mountTab(props: Record<string, unknown> = {}) {
  return mount(FaceRegisterTab, {
    props: { offline: false, ...props },
    global: { plugins: [ArcoVue] },
  })
}

beforeEach(() => {
  mocks.fetchFaceRegisterPlan.mockReset()
  mocks.registerFaceBatch.mockReset()
  mocks.fetchFaceRegisterPlan.mockResolvedValue(PLAN)
  mocks.registerFaceBatch.mockResolvedValue({
    items: [{ person_id: 192, ok: true, face_count: 3, error: null }],
    succeeded: 1,
    failed: 0,
  })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('FaceRegisterTab', () => {
  it('加载计划并展示候选簇与建议素材', async () => {
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain('示例博主')
    expect(wrapper.text()).toContain('出现 18 次')
    expect(wrapper.emitted()).toBeTruthy()
  })

  it('采用单条调用 register-batch 并通知刷新', async () => {
    const wrapper = mountTab()
    await flushPromises()
    const adopt = wrapper.findAll('button').find((b) => b.text() === '采用')
    await adopt!.trigger('click')
    await flushPromises()
    expect(mocks.registerFaceBatch).toHaveBeenCalledWith([
      { person_type: 'blogger', person_id: 192, inspiration_ids: ['a', 'b', 'c'] },
    ])
    expect(wrapper.emitted('registered')).toBeTruthy()
    expect(wrapper.text()).toContain('已注册')
  })

  it('采用当前页按 5 位拆请求，避免单次超时', async () => {
    const six = Array.from({ length: 6 }, (_, idx) => ({
      ...PLAN.items[0],
      person_id: 100 + idx,
      name: `博主${idx}`,
    }))
    mocks.fetchFaceRegisterPlan.mockResolvedValue({
      items: six,
      summary: { ...PLAN.summary, with_candidates: 6, no_candidates: 0 },
    })
    mocks.registerFaceBatch.mockImplementation(async (items: Array<{ person_id: number }>) => ({
      items: items.map((i) => ({ person_id: i.person_id, ok: true, face_count: 3, error: null })),
      succeeded: items.length,
      failed: 0,
    }))
    const wrapper = mountTab()
    await flushPromises()
    const button = wrapper.findAll('button').find((b) => b.text().includes('采用当前页'))
    await button!.trigger('click')
    await flushPromises()
    expect(mocks.registerFaceBatch).toHaveBeenCalledTimes(2)
    expect(mocks.registerFaceBatch.mock.calls[0][0]).toHaveLength(5)
    expect(mocks.registerFaceBatch.mock.calls[1][0]).toHaveLength(1)
  })

  it('离线时采用按钮禁用', async () => {
    const wrapper = mountTab({ offline: true })
    await flushPromises()
    const adopt = wrapper.findAll('button').find((b) => b.text() === '采用')
    expect(adopt?.attributes('disabled')).toBeDefined()
  })
})
