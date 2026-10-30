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
  fetchInspirations: vi.fn(),
  autoTitleRun: vi.fn(),
}))

vi.mock('@/api/generate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/generate')>()
  return { ...actual, fetchRun: mocks.fetchRun, autoTitleRun: mocks.autoTitleRun }
})

vi.mock('@/api/inspirations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/inspirations')>()
  return { ...actual, fetchInspirations: mocks.fetchInspirations }
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

  it('换姿势批次在展开行显示姿势 id（一键出图几百批时靠它区分）', async () => {
    mocks.fetchRun.mockResolvedValue({ run: {}, items: [], task: null })
    const wrapper = mountTable(
      record({ params: { ...record().params, pose_id: 'full_sexy_P001_side' } }),
    )
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)

    expect(wrapper.text()).toContain('姿势 full_sexy_P001_side')
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

// ── P0：日期范围 ──

describe('生成历史表 · 日期范围', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('选日期范围回抛 since 与「次日零点」的 until（用户选 10-26 要含 26 号全天）', async () => {
    const wrapper = mountTable(record(), { filters: {} })
    await wrapper.vm.$nextTick()

    // 直接调用组件的日期回调（Arco RangePicker 的弹层在 jsdom 里不稳定，组件已 expose）
    const vm = wrapper.vm as unknown as {
      onDateRangeChange: (v: unknown) => void
    }
    vm.onDateRangeChange(['2026-10-20', '2026-10-26'])

    const emitted = wrapper.emitted('update:filters') ?? []
    const last = emitted.at(-1)?.[0] as Record<string, unknown>
    expect(last.since).toBe('2026-10-20')
    // 结束日 +1 天：后端按 created_at < until 过滤，这样才包含 26 号全天
    expect(last.until).toBe('2026-10-27')

    // 清空日期 → 两个字段都撤掉
    vm.onDateRangeChange([])
    const cleared = (wrapper.emitted('update:filters') ?? []).at(-1)?.[0] as Record<string, unknown>
    expect(cleared.since).toBeUndefined()
    expect(cleared.until).toBeUndefined()

    wrapper.unmount()
  })
})

// ── P1：收藏 / 保留期 / 统计卡 / 带入表单 / 入库反查 / 标注 ──

describe('生成历史表 · 标注与统计', () => {
  beforeEach(() => {
    mocks.fetchRun.mockReset()
    mocks.fetchRun.mockResolvedValue({ run: {}, items: [], task: null })
    document.body.innerHTML = ''
  })

  it('点星标回抛 toggle-favorite（收藏的批次不会被清理）', async () => {
    const wrapper = mountTable(record({ favorite: false }))
    await wrapper.vm.$nextTick()

    await wrapper.find('.gh-star').trigger('click')
    expect(wrapper.emitted('toggle-favorite')?.[0]).toEqual([12, true])

    wrapper.unmount()
  })

  it('保留期文案：收藏 / 已清理 / 剩余天数', async () => {
    const fav = mountTable(record({ favorite: true }))
    await fav.vm.$nextTick()
    expect(fav.text()).toContain('已收藏，不清理')
    fav.unmount()

    const gone = mountTable(record({ files_missing: true, candidates: [] }))
    await gone.vm.$nextTick()
    expect(gone.text()).toContain('图已清理')
    gone.unmount()

    const soon = new Date(Date.now() + 3 * 86_400_000).toISOString()
    const expiring = mountTable(record({ expires_at: soon }))
    await expiring.vm.$nextTick()
    expect(expiring.text()).toContain('3 天后清理')
    expiring.unmount()
  })

  it('统计卡渲染批次/出图/入库率/均耗时/失败率与档位分布', async () => {
    const wrapper = mountTable(record(), {
      stats: {
        days: 30,
        runs: 89,
        images: 188,
        imported_images: 10,
        import_rate: 0.0532,
        avg_duration_s: 56.8,
        failure_rate: 0.0112,
        by_model: [{ model_key: 'qwen_2_1_i2i', runs: 79, images: 170 }],
      },
    })
    await wrapper.vm.$nextTick()

    const card = wrapper.find('.gh-stats')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('89')
    expect(card.text()).toContain('188')
    expect(card.text()).toContain('10')
    expect(card.text()).toContain('5.3%')
    expect(card.text()).toContain('56.8s')
    expect(card.text()).toContain('1.1%')
    expect(card.findAll('.gh-stat-model')).toHaveLength(1)

    wrapper.unmount()
  })

  it('「带入表单」回抛该行 id（不直接生成）', async () => {
    const wrapper = mountTable(record())
    await wrapper.vm.$nextTick()

    const buttons = wrapper.findAll('button').filter((b) => b.text() === '带入表单')
    expect(buttons).toHaveLength(1)
    await buttons[0].trigger('click')

    expect(wrapper.emitted('apply-to-form')?.[0]).toEqual([12])
    wrapper.unmount()
  })

  it('「已入库 N 张」可点，回抛 open-imported', async () => {
    const wrapper = mountTable(record({ imported_count: 2 }))
    await wrapper.vm.$nextTick()

    const chip = wrapper.find('.gh-imported')
    expect(chip.exists()).toBe(true)
    await chip.trigger('click')

    expect(wrapper.emitted('open-imported')?.[0]).toEqual([12])
    wrapper.unmount()
  })

  it('标注弹窗：保存后回抛 save-meta（含命名与备注）', async () => {
    const wrapper = mountTable(record(), { attachTo: document.body })
    await wrapper.vm.$nextTick()

    const btn = wrapper.findAll('button').filter((b) => b.text() === '标注')
    expect(btn).toHaveLength(1)
    await btn[0].trigger('click')
    await wrapper.vm.$nextTick()

    // 弹窗 teleport 到 body：用 document 查
    const inputs = Array.from(
      document.querySelectorAll('.gh-meta-form input'),
    ) as HTMLInputElement[]
    const textareas = Array.from(
      document.querySelectorAll('.gh-meta-form textarea'),
    ) as HTMLTextAreaElement[]
    expect(inputs.length).toBeGreaterThan(0)
    expect(textareas.length).toBeGreaterThan(0)

    inputs[0].value = '通勤白衬衫'
    inputs[0].dispatchEvent(new Event('input'))
    textareas[0].value = '袖子加长'
    textareas[0].dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    const save = Array.from(document.querySelectorAll('.gh-meta-actions button')).find((el) =>
      (el as HTMLElement).textContent?.includes('保存'),
    ) as HTMLElement
    expect(save).toBeTruthy()
    save.click()
    await wrapper.vm.$nextTick()

    const emitted = wrapper.emitted('save-meta')?.[0] as [number, Record<string, unknown>]
    expect(emitted?.[0]).toBe(12)
    expect(emitted?.[1].title).toBe('通勤白衬衫')
    expect(emitted?.[1].note).toBe('袖子加长')
    expect(emitted?.[1].clear_title).toBe(false)

    wrapper.unmount()
  })

  // ── P2：导出 / 参考素材聚合 ──

  it('导出下拉：CSV / JSON 两种格式都回抛', async () => {
    // Arco 的 dropdown content 是懒挂载（jsdom 里不悬浮就不渲染）→ 用透传 stub 把内容渲染出来
    const DropdownStub = {
      name: 'ADropdownStub',
      template: '<div class="dd-stub"><slot name="content" /></div>',
    }
    const wrapper = mount(GenerateHistoryTable, {
      props: {
        history: [record()],
        total: 1,
        page: 1,
        size: 20,
        filters: {},
        loading: false,
        submitting: false,
        deletingId: null,
        status,
      },
      global: { plugins: [ArcoVue], stubs: { 'a-dropdown': DropdownStub } },
    })
    await wrapper.vm.$nextTick()

    // Arco 下拉项的类名是 arco-dropdown-option（不是 arco-doption）
    const options = wrapper.findAll('.dd-stub .arco-dropdown-option')
    expect(options).toHaveLength(2)
    await options[0].trigger('click')
    await options[1].trigger('click')

    expect(wrapper.emitted('export')).toEqual([['csv'], ['json']])
    wrapper.unmount()
  })

  it('参考素材缩略图：点一张回抛 filter-by-reference', async () => {
    mocks.fetchInspirations.mockResolvedValue({
      items: [
        { id: 'ref-a', file_path: 'images/ref-a.jpg', thumbnail_path: 'thumbnails/ref-a.jpg' },
        { id: 'ref-b', file_path: 'images/ref-b.jpg', thumbnail_path: null },
      ],
      total: 2,
    })
    const wrapper = mountTable(
      record({ params: { reference_ids: ['ref-a', 'ref-b'], width: 1024, height: 768 } }),
    )
    await wrapper.vm.$nextTick()
    await expandFirstRow(wrapper)
    await flushPromises()

    expect(mocks.fetchInspirations).toHaveBeenCalledWith({ ids: 'ref-a,ref-b', size: 2 })
    const thumbs = wrapper.findAll('.ghd-ref')
    expect(thumbs).toHaveLength(2)
    // 有 thumbnail_path 用缩略图，否则退回原图
    expect(thumbs[0].find('img').attributes('src')).toContain('thumbnails/ref-a.jpg')
    expect(thumbs[1].find('img').attributes('src')).toContain('images/ref-b.jpg')

    await thumbs[1].trigger('click')
    expect(wrapper.emitted('filter-by-reference')?.[0]).toEqual(['ref-b'])

    wrapper.unmount()
  })

  it('参考素材筛选生效时显示可关闭的标签，关闭即清掉该筛选', async () => {
    const wrapper = mountTable(record(), { filters: { reference_id: 'ref-a-long-id' } })
    await wrapper.vm.$nextTick()

    const tag = wrapper.find('.gh-toolbar .arco-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toContain('只看这张参考图')

    await tag.find('.arco-tag-close-btn').trigger('click')
    const emitted = wrapper.emitted('update:filters') ?? []
    expect((emitted.at(-1)?.[0] as Record<string, unknown>).reference_id).toBeUndefined()

    wrapper.unmount()
  })

  // ── P2：两批对比（C1）与 AI 起名（C3） ──

  it('选两个批次后「对比这两批」才可用，点击回抛两个 id', async () => {
    const wrapper = mount(GenerateHistoryTable, {
      props: {
        history: [record({ id: 11 }), record({ id: 22 })],
        total: 2,
        page: 1,
        size: 20,
        filters: {},
        loading: false,
        submitting: false,
        deletingId: null,
        status,
      },
      global: { plugins: [ArcoVue] },
      attachTo: document.body,
    })
    await wrapper.vm.$nextTick()

    const compareBtn = wrapper.findAll('button').filter((b) => b.text() === '对比这两批')
    expect(compareBtn).toHaveLength(1)
    expect(compareBtn[0].attributes('disabled')).toBeDefined()

    const rowCompare = wrapper.findAll('button').filter((b) => b.text() === '对比')
    expect(rowCompare).toHaveLength(2)
    await rowCompare[0].trigger('click')
    await rowCompare[1].trigger('click')

    const submit = wrapper.findAll('button').filter((b) => b.text().includes('对比这两批'))
    expect(submit[0].attributes('disabled')).toBeUndefined()
    await submit[0].trigger('click')

    expect(wrapper.emitted('compare-runs')?.[0]).toEqual([[11, 22]])
    wrapper.unmount()
  })

  it('同一个批次点两次「对比」= 取消选择', async () => {
    const wrapper = mountTable(record({ id: 33 }))
    await wrapper.vm.$nextTick()

    const btn = wrapper.findAll('button').filter((b) => b.text() === '对比')
    await btn[0].trigger('click')
    expect(wrapper.findAll('button').some((b) => b.text() === '已选对比')).toBe(true)

    await wrapper
      .findAll('button')
      .filter((b) => b.text() === '已选对比')[0]
      .trigger('click')
    expect(wrapper.findAll('button').some((b) => b.text() === '对比')).toBe(true)

    wrapper.unmount()
  })

  it('「AI 起名」把模型建议填进命名框（仍需用户点保存）', async () => {
    mocks.autoTitleRun.mockResolvedValue({ title: '通勤白衬衫', source: 'ai' })
    const wrapper = mountTable(record(), { attachTo: document.body })
    await wrapper.vm.$nextTick()

    await wrapper
      .findAll('button')
      .filter((b) => b.text() === '标注')[0]
      .trigger('click')
    await wrapper.vm.$nextTick()

    const autoBtn = Array.from(document.querySelectorAll('.gh-meta-form button')).find((el) =>
      (el as HTMLElement).textContent?.includes('AI 起名'),
    ) as HTMLElement
    expect(autoBtn).toBeTruthy()
    autoBtn.click()
    await flushPromises()

    expect(mocks.autoTitleRun).toHaveBeenCalledWith(12)
    const input = document.querySelector('.gh-meta-form input') as HTMLInputElement
    expect(input.value).toBe('通勤白衬衫')
    // 没点保存：不应该回抛 save-meta
    expect(wrapper.emitted('save-meta')).toBeUndefined()

    wrapper.unmount()
  })
})
