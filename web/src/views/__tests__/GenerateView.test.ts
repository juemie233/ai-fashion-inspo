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
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/composables/useImageGen', async () => {
  // 用真实 ref：模板里会把 ref 自动解包，返回普通对象会让 `status.comfyui` 取不到
  const { ref } = await import('vue')
  return {
    useImageGen: () => ({
      status: ref({
        ready: true,
        reason: null,
        comfyui: { version: '0.42.1', vram_total_gib: 15.9, vram_free_gib: 14.8 },
        models: { z_image_int8: 'ok', qwen_2_1_i2i: 'ok' },
        ollama_resident: [],
      }),
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
})
