/**
 * 参数面板的档位时序：**探测未完成时也要把「从详情页指定档位」选上**。
 *
 * 真实缺陷（2026-10-24 用户报）：从素材详情点「图生图（未审查）」跳进来，
 * `/generate/status` 要探测 ComfyUI + 权重文件（实测 **4.4 秒**），这期间面板只有
 * 内置的「兜底两档」，里面没有未审查档位：
 * - 页面先闪出两档、几秒后第三档才冒出来（观感问题）；
 * - 更严重：请求的档位当次没找到就被丢弃 → 到页面时**没有选中未审查版**。
 *
 * 现在的约定：
 * 1. 请求的档位只「真正命中」时才认领，列表晚到也能补选；
 * 2. 探测未回来时用上次的目录缓存渲染档位（就绪状态仍等探测）；没有缓存则显示「正在读取」，
 *    不再渲染假档位。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import GenerateParamForm from '../GenerateParamForm.vue'
import type { GenerateStatus } from '@/api/generate'

/** 后端 MODEL_SPECS 的三档目录（探测结果里就是这样下发的） */
const MODELS_META = {
  z_image_int8: { label: 'Z-Image-Turbo int8（快）', kind: 't2i', defaults: {} },
  qwen_2_1_i2i: { label: 'Qwen-Image 2.1（参考图改款）', kind: 'i2i', defaults: {} },
  qwen_2_1_uc_i2i: {
    label: 'Qwen-Image 2.1 未审查版（参考图改款）',
    kind: 'i2i',
    defaults: {},
    uncensored: true,
    notice: '该档位使用无内容审查的权重',
  },
} as GenerateStatus['models_meta']

const READY_STATUS: GenerateStatus = {
  ready: true,
  reason: null,
  comfyui: { version: '0.42.1', vram_total_gib: 15.9, vram_free_gib: 14.8 },
  models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok', qwen_2_1_uc_i2i: 'ok' },
  ollama_resident: [],
  models_meta: MODELS_META,
}

function mountForm(props: Record<string, unknown> = {}) {
  return mount(GenerateParamForm, {
    props: {
      status: null,
      ready: false,
      submitting: false,
      active: false,
      initialRefIds: '',
      promptFrom: vi.fn(),
      ...props,
    },
    global: { plugins: [ArcoVue] },
  })
}

function checkedValue(wrapper: ReturnType<typeof mountForm>): string | undefined {
  const checked = wrapper
    .findAll('input[type="radio"]')
    .find((el) => (el.element as HTMLInputElement).checked)
  return checked?.attributes('value')
}

describe('GenerateParamForm 档位时序', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('探测未返回：不渲染假档位，只说明在等什么', async () => {
    const wrapper = mountForm()
    await flushPromises()

    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('正在读取模型档位')
  })

  it('回归：请求的未审查档位不会因「首次列表里没有」而被丢弃', async () => {
    const wrapper = mountForm({
      initialModelKey: 'qwen_2_1_uc_i2i',
      initialRefIds: 'insp-abc',
    })
    await flushPromises()
    // 探测还没回来：档位列表为空，此时不能认领、也不能清空请求
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)

    await wrapper.setProps({ status: READY_STATUS, ready: true })
    await flushPromises()

    expect(checkedValue(wrapper)).toBe('qwen_2_1_uc_i2i')
    // 风险提示随未审查档位出现
    expect(wrapper.text()).toContain('该档位使用无内容审查的权重')
    // 参考素材 ID 也已回填
    const refInput = wrapper.find('input[placeholder*="参考素材 ID"]')
    expect((refInput.element as HTMLInputElement).value).toBe('insp-abc')
  })

  it('有目录缓存时进页面立即可选（不用等 4 秒探测），就绪状态仍等探测', async () => {
    const wrapper = mountForm({
      initialModelKey: 'qwen_2_1_uc_i2i',
      modelsMetaFallback: MODELS_META,
    })
    await flushPromises()

    // 立刻就能看到三档，并且已选中未审查版
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(3)
    expect(checkedValue(wrapper)).toBe('qwen_2_1_uc_i2i')
    expect(wrapper.text()).toContain('就绪状态正在探测')

    // 探测回来后档位不变（就绪状态变成真实值，不会把用户的选择顶掉）
    await wrapper.setProps({ status: READY_STATUS, ready: true })
    await flushPromises()
    expect(checkedValue(wrapper)).toBe('qwen_2_1_uc_i2i')
    expect(wrapper.text()).not.toContain('就绪状态正在探测')
  })

  it('没指定档位时仍默认挑「就绪且非未审查」的（不打乱原规则）', async () => {
    const wrapper = mountForm({ modelsMetaFallback: MODELS_META })
    await flushPromises()
    expect(checkedValue(wrapper)).toBe('z_image_int8')
  })

  it('回归：只带 initialRefIds（「用这张图改款」）且档位表晚到时，表到齐后自动选图生图', async () => {
    // 首访无目录缓存：探测未返回时 allModels 为空，onMounted 的 selectModelByKind 是空操作
    const wrapper = mountForm({ initialRefIds: 'abc123' })
    await flushPromises()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)

    // 探测回来（后端顺序里 t2i 排第一，若按「就绪且非未审查」会挑到文生图 → 参考图静默失效）
    await wrapper.setProps({ status: READY_STATUS, ready: true })
    await flushPromises()

    expect(checkedValue(wrapper)).toBe('qwen_2_1_i2i')
  })
})

describe('已选参考图缩略图（描述框上方）', () => {
  const REFS = [
    { id: 'ref-a', file_path: 'images/a.jpg', thumbnail_path: 'thumbnails/a.jpg' },
    { id: 'ref-b', file_path: 'images/b.jpg', thumbnail_path: null },
  ]

  it('图生图下渲染缩略图条：编号、顺序与提示', async () => {
    const wrapper = mountForm({
      status: READY_STATUS,
      ready: true,
      initialRefIds: 'ref-a, ref-b',
      referenceItems: REFS,
    })
    await flushPromises()

    const thumbs = wrapper.findAll('.gen-ref-thumb')
    expect(thumbs).toHaveLength(2)
    // 编号即参考顺序（第 1 张是主参考）
    expect(thumbs[0].find('.gen-ref-thumb-index').text()).toBe('1')
    expect(thumbs[1].find('.gen-ref-thumb-index').text()).toBe('2')
    // 缩略图优先用 thumbnail_path，缺了退回原图
    expect(thumbs[0].find('img').attributes('src')).toContain('thumbnails/a.jpg')
    expect(thumbs[1].find('img').attributes('src')).toContain('images/b.jpg')
    expect(wrapper.text()).toContain('已选参考（2）')
  })

  it('点某张缩略图抛出下标（视图据此打开大图并定位）', async () => {
    const wrapper = mountForm({
      status: READY_STATUS,
      ready: true,
      initialRefIds: 'ref-a, ref-b',
      referenceItems: REFS,
    })
    await flushPromises()

    await wrapper.findAll('.gen-ref-thumb')[1].trigger('click')
    expect(wrapper.emitted('browse-references')?.[0]).toEqual([1])
  })

  it('参考 id 变化时抛给视图（视图据此拉缩略图），挂载时也抛一次', async () => {
    const wrapper = mountForm({ status: READY_STATUS, ready: true, initialRefIds: 'ref-a' })
    await flushPromises()
    // 首次渲染（此时还没读到 initialRefIds）会先抛一次空数组，onMounted 预填后补发真实的
    expect((wrapper.emitted('reference-ids-change') ?? []).at(-1)?.[0]).toEqual(['ref-a'])

    // 手输多个 id：同样抛出去（顺序保持不变）
    const input = wrapper.find('input[placeholder*="参考素材 ID"]')
    await input.setValue('ref-a, ref-b')
    const events = wrapper.emitted('reference-ids-change') ?? []
    expect(events.at(-1)?.[0]).toEqual(['ref-a', 'ref-b'])
  })

  it('没有已选参考时不渲染缩略图条（不占描述框的空间）', async () => {
    const wrapper = mountForm({ status: READY_STATUS, ready: true, referenceItems: [] })
    await flushPromises()
    expect(wrapper.find('.gen-ref-strip').exists()).toBe(false)
  })
})
