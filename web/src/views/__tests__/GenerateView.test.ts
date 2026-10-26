/**
 * 本地出图页（GenerateView）参数透传回归测试。
 *
 * 覆盖一处历史缺陷：宽高原先写成 `const [width, height] = computed(...).value`，
 * 那会在 setup 时求值一次并把 1024/1024 固定下来 —— 用户切换尺寸预设（1328²/竖版）
 * 完全不生效，请求永远按 1024² 发。这里断言「切换预设后 payload 的宽高真的变了」。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue, { Select } from '@arco-design/web-vue'
import GenerateView from '../GenerateView.vue'

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  loadStatus: vi.fn(),
  loadHistory: vi.fn(),
  /** 出图页读到的就绪状态（ref）：测试可整体替换，用来模拟「多档位 / 未审查档位」 */
  statusRef: { value: {} as Record<string, unknown> },
  /** 初始状态快照：每个用例开始前还原，避免用例之间互相污染 */
  defaultStatus: {} as Record<string, unknown>,
  /** 路由 query（可变）：模拟「从素材详情带 ref / model 跳进来」 */
  routeQuery: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/composables/useImageGen', async () => {
  // 用真实 ref：模板里会把 ref 自动解包，返回普通对象会让 `status.comfyui` 取不到
  const { ref } = await import('vue')
  const status = ref({
    ready: true,
    reason: null,
    comfyui: { version: '0.42.1', vram_total_gib: 15.9, vram_free_gib: 14.8 },
    models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok' },
    ollama_resident: [],
  })
  mocks.statusRef = status as unknown as { value: Record<string, unknown> }
  mocks.defaultStatus = JSON.parse(JSON.stringify(status.value))
  return {
    useImageGen: () => ({
      status,
      statusLoading: ref(false),
      submitting: ref(false),
      run: ref(null),
      candidates: ref([]),
      selected: ref(new Set<string>()),
      active: ref(false),
      stageText: ref(''),
      taskStage: ref({ stage: '', waited: 0, vramFree: null }),
      taskInfo: ref(null),
      lastSeed: ref(null),
      history: ref([]),
      historyLoading: ref(false),
      loadStatus: mocks.loadStatus,
      submit: mocks.submit,
      toggle: vi.fn(),
      selectAll: vi.fn(),
      clearSelection: vi.fn(),
      confirm: vi.fn(),
      cancel: vi.fn(),
      promptFrom: vi.fn(),
      discard: vi.fn(),
      stopPoll: vi.fn(),
      loadHistory: mocks.loadHistory,
      retry: vi.fn(),
    }),
  }
})

function mountView() {
  return mount(GenerateView, { global: { plugins: [ArcoVue] } })
}

describe('GenerateView 参数透传', () => {
  beforeEach(() => {
    mocks.submit.mockReset()
    mocks.submit.mockResolvedValue(1)
    mocks.loadHistory.mockReset()
    mocks.statusRef.value = JSON.parse(JSON.stringify(mocks.defaultStatus))
    mocks.routeQuery = {}
  })

  it('默认按 1024×1024 提交', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('textarea').setValue('米色针织开衫')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()

    expect(mocks.submit).toHaveBeenCalledTimes(1)
    const payload = mocks.submit.mock.calls[0][0]
    expect(payload.width).toBe(1024)
    expect(payload.height).toBe(1024)
    expect(payload.kind).toBe('t2i')
  })

  it('切换到竖版预设后，宽高跟着变（回归：曾经被固定成 1024）', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 直接改内部 ref 不可行（script setup 不暴露），走「向下拉组件派发 v-model 更新」
    // 这一步等价于用户选中「768 × 1344（竖版）」——正是当年失效的那个交互
    const select = wrapper.findComponent(Select)
    expect(select.exists()).toBe(true)
    select.vm.$emit('update:modelValue', '768x1344')
    await flushPromises()

    await wrapper.find('textarea').setValue('竖版穿搭')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()

    const payload = mocks.submit.mock.calls[0][0]
    expect(payload.width).toBe(768)
    expect(payload.height).toBe(1344)
  })

  it('i2i 模式下把参考素材 id 一起提交', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 切到图生图
    const radios = wrapper.findAll('input[type="radio"]')
    await radios[1].setValue(true)
    await flushPromises()

    // 参考素材输入框（i2i 模式下的第一个 input）
    const refInput = wrapper.find('input[placeholder*="参考素材 ID"]')
    expect(refInput.exists()).toBe(true)
    await refInput.setValue('id-a, id-b')
    await wrapper.find('textarea').setValue('把这些单品组合成一套穿搭')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()

    const payload = mocks.submit.mock.calls[0][0]
    expect(payload.kind).toBe('i2i')
    expect(payload.reference_ids).toEqual(['id-a', 'id-b'])
  })

  it('模型选择器常驻：默认非未审查档位，切到未审查版要打标、给警示并传 model_key', async () => {
    // 三个档位（与后端 MODEL_SPECS 一致）：文生图 + 改款标准 + 改款未审查
    mocks.statusRef.value = {
      ready: true,
      reason: null,
      comfyui: { version: '0.42.1', vram_total_gib: 15.9, vram_free_gib: 14.8 },
      models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok', qwen_2_1_uc_i2i: 'ok' },
      ollama_resident: [],
      models_meta: {
        z_image_int8: { label: 'Z-Image-Turbo（快）', kind: 't2i', defaults: {} },
        qwen_2_1_i2i: { label: 'Qwen-Image 2.1（参考图改款）', kind: 'i2i', defaults: {} },
        qwen_2_1_uc_i2i: {
          label: 'Qwen-Image 2.1 未审查版（参考图改款）',
          kind: 'i2i',
          defaults: {},
          uncensored: true,
          notice: '该档位没有内容安全过滤',
        },
      },
    }
    const wrapper = mountView()
    await flushPromises()

    // 回归：选择器必须**一进页面就在**，不能藏在「参考图改款」下面
    const radios = wrapper.findAll('input[type="radio"]')
    expect(radios.length).toBe(3)
    expect(wrapper.text()).toContain('未审查')

    // 默认文生图（第一档）：不选未审查那个
    await wrapper.find('textarea').setValue('米色针织开衫')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][0].kind).toBe('t2i')

    // 选改款标准档 → 出现参考图输入，提交带该档位
    mocks.submit.mockClear()
    await radios[1].setValue(true)
    await flushPromises()
    await wrapper.find('input[placeholder*="参考素材 ID"]').setValue('id-a')
    await submitBtn.trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][0].model_key).toBe('qwen_2_1_i2i')
    expect(mocks.submit.mock.calls[0][0].kind).toBe('i2i')

    // 主动选未审查版 → 出现风险提示，且提交时带上该档位
    mocks.submit.mockClear()
    await radios[2].setValue(true)
    await flushPromises()
    expect(wrapper.text()).toContain('该档位没有内容安全过滤')
    await submitBtn.trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][0].model_key).toBe('qwen_2_1_uc_i2i')
  })
  it('生成历史默认收起：不渲染表格、也不发请求', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.gen-history').exists()).toBe(false)
    expect(mocks.loadHistory).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('展开生成历史')
  })

  it('展开才加载历史；收起再展开不重复请求', async () => {
    const wrapper = mountView()
    await flushPromises()

    const toggle = wrapper.findAll('button').find((b) => b.text().includes('生成历史'))!
    await toggle.trigger('click')
    await flushPromises()

    expect(mocks.loadHistory).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.gen-history').exists()).toBe(true)
    expect(wrapper.text()).toContain('收起生成历史')

    await toggle.trigger('click') // 收起：只隐藏，不请求
    await flushPromises()
    expect(wrapper.find('.gen-history').exists()).toBe(false)

    await toggle.trigger('click') // 再展开：已有数据，不重复拉
    await flushPromises()
    expect(mocks.loadHistory).toHaveBeenCalledTimes(1)
  })

  it('从素材详情带 ref + model 跳进来：切到未审查档位并预填参考 ID', async () => {
    // 后端多返回一个未审查档位（真实环境里它由 MODEL_SPECS 带 uncensored 标记下发）
    mocks.statusRef.value = {
      ...JSON.parse(JSON.stringify(mocks.defaultStatus)),
      models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok', qwen_2_1_uc_i2i: 'ok' },
      models_meta: {
        z_image_int8: { label: 'Z-Image-Turbo int8（快）', kind: 't2i', defaults: {} },
        qwen_2_1_i2i: { label: 'Qwen-Image 2.1（参考图改款）', kind: 'i2i', defaults: {} },
        qwen_2_1_uc_i2i: {
          label: 'Qwen-Image 2.1 未审查版（参考图改款）',
          kind: 'i2i',
          defaults: {},
          uncensored: true,
          notice: '该档位使用无内容审查的权重',
        },
      },
    }
    mocks.routeQuery = { ref: 'insp-abc', model: 'qwen_2_1_uc_i2i' }

    const wrapper = mountView()
    await flushPromises()

    // 参考素材 ID 已预填
    const refInput = wrapper.find('input[placeholder*="参考素材 ID"]')
    expect(refInput.exists()).toBe(true)
    expect((refInput.element as HTMLInputElement).value).toBe('insp-abc')

    // 选中的是未审查档位（而不是默认那个非未审查的图生图档位）
    const ucRadio = wrapper.find('input[type="radio"][value="qwen_2_1_uc_i2i"]')
    expect(ucRadio.exists()).toBe(true)
    expect((ucRadio.element as HTMLInputElement).checked).toBe(true)

    // 风险提示随档位出现（未审查权重的 notice 由后端下发）
    expect(wrapper.text()).toContain('该档位使用无内容审查的权重')

    // 提交时带上这个档位与参考 ID
    await wrapper.find('textarea').setValue('同一个人，换一套更利落的裤子')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()
    // 用「最后一次提交」断言：前面的用例把 wrapper 留在同一份 mock 上（VTU 不会自动卸载），
    // 取 calls[0] 可能读到别人的历史 payload
    expect(mocks.submit).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: 'i2i',
        model_key: 'qwen_2_1_uc_i2i',
        reference_ids: ['insp-abc'],
        prompt: '同一个人，换一套更利落的裤子',
      }),
    )
  })
})
