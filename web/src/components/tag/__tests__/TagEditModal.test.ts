/**
 * TagEditModal 回归测试：父组件用 v-if + 固定 :show="true" 挂载编辑弹窗，
 * 表单必须在挂载时立即回填——否则保存时名称为空会静默 return、前端不发请求
 * （历史缺陷：watch(show) 无 immediate，组件每次全新挂载 show 恒为 true，
 * watch 永不触发，编辑功能整体瘫痪）。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 测试桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { Message } from '@arco-design/web-vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TagEditModal from '../TagEditModal.vue'
import { updateTag, type TagItem } from '@/api/tags'

vi.mock('@/api/tags', () => ({
  updateTag: vi.fn(() => Promise.resolve({})),
}))

// ===== Arco 组件最小桩（带显式 name，供 VTU 按引用查找） =====
const slotDiv = defineComponent({
  name: 'SlotDiv',
  render() {
    return h('div', this.$slots.default?.())
  },
})
const inputStub = defineComponent({
  name: 'AInput',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  render() {
    return h('input')
  },
})
const selectStub = defineComponent({
  name: 'ASelect',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  // 不用原生 <select>：父组件下发 options 会触发「options 只读」告警，测试里
  // 不需要真实下拉，改用 div 承载即可
  render() {
    return h('div')
  },
})
const textareaStub = defineComponent({
  name: 'ATextarea',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  render() {
    return h('textarea')
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', { type: 'button' }, this.$slots.default?.())
  },
})

// 示例对象：用户反馈中的「蕾丝花边吊袜带」类 AI 生成标签
const TAG: TagItem = {
  id: 7,
  name: '蕾丝花边吊袜带',
  category: 'item_type',
  source: 'ai_generated',
  pinned: false,
  sort_order: 0,
  description: null,
  usage_count: 3,
}

function mountModal(tag: TagItem | null = TAG) {
  return mount(TagEditModal, {
    props: { show: true, tag },
    global: {
      stubs: {
        'a-modal': slotDiv,
        'a-form': slotDiv,
        'a-form-item': slotDiv,
        'a-input': inputStub,
        'a-select': selectStub,
        'a-textarea': textareaStub,
        'a-space': slotDiv,
        'a-button': buttonStub,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TagEditModal（编辑标签弹窗）', () => {
  it('挂载即回填：点保存会发出更新请求（id=7），证明表单非空', async () => {
    const wrapper = mountModal()
    const nameInput = wrapper.findAllComponents(inputStub)[0]
    expect(nameInput.exists()).toBe(true)

    // 点击「保存」按钮（0=取消 1=保存）→ 必须调用 updateTag
    const saveBtn = wrapper.findAllComponents(buttonStub)[1]
    await saveBtn.trigger('click')

    expect(updateTag).toHaveBeenCalledTimes(1)
    expect(updateTag).toHaveBeenCalledWith(7, {
      name: undefined,
      category: undefined,
      description: undefined,
    })
  })

  it('改名后保存：把新名称随请求发出', async () => {
    const wrapper = mountModal()
    const nameInput = wrapper.findAllComponents(inputStub)[0]
    await nameInput.setValue('黑色吊带袜')

    const saveBtn = wrapper.findAllComponents(buttonStub)[1]
    await saveBtn.trigger('click')

    expect(updateTag).toHaveBeenCalledWith(7, {
      name: '黑色吊带袜',
      category: undefined,
      description: undefined,
    })
  })

  it('名称为空时保存：不发请求并给出提示（不再静默瘫痪）', async () => {
    const warningSpy = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const wrapper = mountModal()
    const nameInput = wrapper.findAllComponents(inputStub)[0]
    await nameInput.setValue('')

    const saveBtn = wrapper.findAllComponents(buttonStub)[1]
    await saveBtn.trigger('click')

    expect(updateTag).not.toHaveBeenCalled()
    expect(warningSpy).toHaveBeenCalledWith('标签名称不能为空')
    warningSpy.mockRestore()
  })
})
