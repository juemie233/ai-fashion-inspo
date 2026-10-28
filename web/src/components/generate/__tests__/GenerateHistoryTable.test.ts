/**
 * 生成历史表：状态中文化、时间格式、展开行详情，以及 P0 新增的四件事。
 *
 * 原有底线（用户反馈过的两个显示问题）：
 * - 时间列曾把 UTC 当本地时间（后端已改为带 Z 的 ISO 串）；
 * - 描述曾截到 120 字，展开行也看不全。
 *
 * P0 新增：
 * 1. 缩略图列——`candidates` 用 `thumb_url`（后端 256px 缩略图），目录已清显示「图已清理」；
 * 2. 「已入库 N 张」标识；
 * 3. 筛选工具栏（档位/状态/只看已入库/关键字防抖）会回抛 `update:filters`；
 * 4. 展开行拉详情、勾选未入库的候选并用 `import-selected` 交给视图入库。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchRun: vi.fn(),
}))

vi.mock('@/api/generate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/generate')>()
  return { ...actual, fetchRun: mocks.fetchRun }
})

import GenerateHistoryTable from '../GenerateHistoryTable.vue'
import type { GenerateStatus, GenerationRunBrief } from '@/api/generate'

const LONG_PROMPT =
  '法式复古碎花雪纺连衣裙，浅蓝色底配白色小碎花，收腰显瘦，适合春季通勤与周末约会，' +
  '搭配米色针织开衫与白色帆布鞋，自然光街拍，柔和背景虚化，突出面料垂坠感。'

const status: GenerateStatus = {
  ready: true,
  reason: null,
  comfyui: {},
  models: {},
  ollama_resident: [],
  models_meta: {
    qwen_2_1_i2i: {
      label: 'Qwen-Image 2.1（参考图改款）',
      kind: 'i2i',
      defaults: {},
    },
  },
}

function record(overrides: Partial<GenerationRunBrief> = {}): GenerationRunBrief {
  return {
    id: 12,
    kind: 'i2i',
    status: 'done',
    prompt: LONG_PROMPT,
    negative: '模糊，低质量，畸形手指',
    params: {
      width: 1024,
      height: 768,
      steps: 24,
      cfg: 4.5,
      sampler: 'euler',
      scheduler: 'normal',
      seed_base: 123456,
      count: 2,
      reference_ids: ['ref-a', 'ref-b'],
    },
    model_key: 'qwen_2_1_i2i',
    workflow_name: 'qwen_image_i2i',
    total: 2,
    done: 2,
    failed: 0,
    error: null,
    created_at: '2026-10-26T12:00:00Z',
    updated_at: '2026-10-26T12:03:00Z',
    ...overrides,
  }
}

function mountTable(row: GenerationRunBrief, extra: Record<string, unknown> = {}) {
  return mount(GenerateHistoryTable, {
    props: {
      history: [row],
      total: 1,
      page: 1,
      size: 20,
      filters: {},
      loading: false,
      submitting: false,
      deletingId: null,
      status,
      ...extra,
    },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

/** 点行首箭头展开 */
async function expandFirstRow(wrapper: ReturnType<typeof mountTable>) {
  const expandBtn = wrapper.find('.arco-table-expand-btn')
  expect(expandBtn.exists()).toBe(true)
  await expandBtn.trigger('click')
  await flushPromises()
  await wrapper.vm.$nextTick()
}

describe('生成历史表', () => {
  beforeEach(() => {
    mocks.fetchRun.mockReset()
    mocks.fetchRun.mockResolvedValue({ run: {}, items: [], task: null })
    document.body.innerHTML = ''
  })

  it('状态显示中文、时间显示为本地格式化结果', async () => {
    const row = record()
    const wrapper = mountTable(row)
    // 列通过 #columns 插槽注册，表体要等一次渲染后才出现
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).not.toContain('done')
    // 写死期望值（测试时区固定 Asia/Shanghai）：12:00Z → 20:00 本地；
    // 若把 Z 丢掉按本地时间解析，这里会显示 12:00，用例就会失败
    expect(wrapper.text()).toContain('2026-10-26 20:00')
    expect(wrapper.text()).not.toContain('2026-10-26 12:00')
    expect(wrapper.text()).not.toContain('Invalid Date')

    wrapper.unmount()
  })

  it('展开行展示完整描述、负面词与全部生成参数', async () => {
    const wrapper = mountTable(record())
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)

    const detail = wrapper.find('[data-test="history-detail"]')
    expect(detail.exists(), '展开后应渲染完整详情').toBe(true)
    const text = detail.text()
    expect(text).toContain(LONG_PROMPT)
    expect(text).toContain('负面提示词')
    expect(text).toContain('模糊，低质量，畸形手指')
    expect(text).toContain('参考图改款')
    expect(text).toContain('尺寸 1024×768')
    expect(text).toContain('步数 24')
    expect(text).toContain('CFG 4.5')
    expect(text).toContain('采样器 euler')
    expect(text).toContain('调度器 normal')
    expect(text).toContain('seed 123456')
    expect(text).toContain('张数 2')
    expect(text).toContain('参考素材 2 张')
    expect(text).toContain('工作流 qwen_image_i2i')
    expect(text).toContain('创建 2026-10-26 20:00')
    expect(text).toContain('最后更新 2026-10-26 20:03')
    expect(text).toContain('复制描述')

    wrapper.unmount()
  })

  it('失败批次展开后能看到失败原因', async () => {
    const wrapper = mountTable(
      record({ status: 'failed', done: 0, error: 'ComfyUI 显存不足（需要 15.1 GiB）' }),
    )
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)

    const detail = wrapper.find('[data-test="history-detail"]')
    expect(detail.text()).toContain('失败原因')
    expect(detail.text()).toContain('ComfyUI 显存不足（需要 15.1 GiB）')

    wrapper.unmount()
  })

  // ── P0：缩略图 / 入库标识 ──

  it('缩略图列用后端给的 thumb_url（不是原图）', async () => {
    const wrapper = mountTable(
      record({
        candidates: [
          {
            filename: '01_seed-1.png',
            thumb_url: '/api/generate/runs/12/candidates/01_seed-1.png?w=256',
          },
          {
            filename: '02_seed-2.png',
            thumb_url: '/api/generate/runs/12/candidates/02_seed-2.png?w=256',
          },
        ],
      }),
    )
    await wrapper.vm.$nextTick()

    const thumbs = wrapper.findAll('img.gh-thumb')
    expect(thumbs).toHaveLength(2)
    expect(thumbs[0].attributes('src')).toContain('?w=256')
    // 懒加载：一屏 20 行不能同时请求 80 张图
    expect(thumbs[0].attributes('loading')).toBe('lazy')

    wrapper.unmount()
  })

  it('候选目录已被清理时显示「图已清理」而不是破图', async () => {
    const wrapper = mountTable(record({ candidates: [], files_missing: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('图已清理')
    expect(wrapper.findAll('img.gh-thumb')).toHaveLength(0)

    wrapper.unmount()
  })

  it('已入库张数显示为角标，未入库不显示', async () => {
    const withImported = mountTable(record({ imported_count: 2 }))
    await withImported.vm.$nextTick()
    expect(withImported.text()).toContain('已入库 2')
    withImported.unmount()

    const none = mountTable(record({ imported_count: 0 }))
    await none.vm.$nextTick()
    expect(none.text()).not.toContain('已入库 0')
    none.unmount()
  })

  // ── P0：筛选工具栏 ──

  it('关键字输入防抖 300ms 后才回抛（不会每敲一个字打一次接口）', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mountTable(record(), { filters: {} })
      await wrapper.vm.$nextTick()

      // 工具栏里还有两个 select 的内部 input：按 placeholder 精确定位关键字框
      const input = wrapper.find('.gh-toolbar input[placeholder="搜描述 / 负面词"]')
      expect(input.exists()).toBe(true)
      await input.setValue('碎花')
      expect(wrapper.emitted('update:filters')).toBeUndefined()

      await vi.advanceTimersByTimeAsync(300)
      const emitted = wrapper.emitted('update:filters') ?? []
      expect((emitted.at(-1)?.[0] as Record<string, unknown>).q).toBe('碎花')

      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  // ── P0：分页 ──

  it('总数超过一页时出现分页并显示总数', async () => {
    const wrapper = mountTable(record(), { total: 45, page: 1, size: 20 })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.gh-pagination').exists()).toBe(true)
    expect(wrapper.text()).toContain('共 45 条')

    wrapper.unmount()
  })

  it('总数不超过一页时不渲染分页', async () => {
    const wrapper = mountTable(record(), { total: 1, page: 1, size: 20 })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.gh-pagination').exists()).toBe(false)

    wrapper.unmount()
  })

  // ── P0：展开行入库 ──

  it('展开行拉详情：已入库只显示角标，未入库可勾选并提交入库', async () => {
    mocks.fetchRun.mockResolvedValue({
      run: {},
      items: [
        { filename: '01_seed-1.png', index: 1, seed: 1, imported: true, url: '/u/1' },
        { filename: '02_seed-2.png', index: 2, seed: 2, imported: false, url: '/u/2' },
      ],
      task: null,
    })
    const wrapper = mountTable(record())
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)

    expect(mocks.fetchRun).toHaveBeenCalledWith(12)
    expect(wrapper.findAll('.ghd-cand')).toHaveLength(2)
    // 已入库那张不可再选：只剩一个复选框
    expect(wrapper.findAll('.ghd-cand .arco-checkbox')).toHaveLength(1)

    await wrapper.find('.ghd-cand .arco-checkbox input').setValue(true)
    await flushPromises()

    const buttons = wrapper.findAll('.ghd-cand-actions button')
    expect(buttons).toHaveLength(1)
    await buttons[0].trigger('click')

    expect(wrapper.emitted('import-selected')?.[0]).toEqual([12, ['02_seed-2.png']])

    wrapper.unmount()
  })

  it('没勾选时入库按钮禁用（不发事件）', async () => {
    mocks.fetchRun.mockResolvedValue({
      run: {},
      items: [{ filename: '01_seed-1.png', index: 1, seed: 1, imported: false, url: '/u/1' }],
      task: null,
    })
    const wrapper = mountTable(record())
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)

    const button = wrapper.find('.ghd-cand-actions button')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('import-selected')).toBeUndefined()

    wrapper.unmount()
  })
})
