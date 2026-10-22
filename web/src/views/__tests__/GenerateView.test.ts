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
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
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
    mocks.statusRef.value = JSON.parse(JSON.stringify(mocks.defaultStatus))
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

  it('i2i 多档位：默认标准版，切到未审查版要打标、给警示并传 model_key', async () => {
    // 两个 i2i 档位：标准 + 未审查（uncensored 标记决定前端是否打标/警示）
    mocks.statusRef.value = {
      ready: true,
      reason: null,
      comfyui: { version: '0.42.1', vram_total_gib: 15.9, vram_free_gib: 14.8 },
      models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok', qwen_2_1_uc_i2i: 'ok' },
      ollama_resident: [],
      models_meta: {
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

    // 切到图生图后才会出现档位选择器
    await wrapper.findAll('input[type="radio"]')[1].setValue(true)
    await flushPromises()
    expect(wrapper.text()).toContain('未审查')

    await wrapper.find('input[placeholder*="参考素材 ID"]').setValue('id-a')
    await wrapper.find('textarea').setValue('改成更利落的版型')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('生成'))!
    await submitBtn.trigger('click')
    await flushPromises()

    // 默认必须是标准版：不能替用户默认选中「无内容过滤」的权重
    expect(mocks.submit.mock.calls[0][0].model_key).toBe('qwen_2_1_i2i')

    // 主动选未审查版 → 出现风险提示，且提交时带上该档位
    mocks.submit.mockClear()
    await wrapper.findAll('input[type="radio"]')[3].setValue(true)
    await flushPromises()
    expect(wrapper.text()).toContain('该档位没有内容安全过滤')
    await submitBtn.trigger('click')
    await flushPromises()
    expect(mocks.submit.mock.calls[0][0].model_key).toBe('qwen_2_1_uc_i2i')
  })
})
