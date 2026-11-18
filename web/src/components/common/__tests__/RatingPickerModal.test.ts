/**
 * 公共评分弹窗（`RatingPickerModal`）测试。
 *
 * 它是「批量评分」与「补全博主评分」共用的选择器，行为差异全在 props：
 * - 批量评分：允许清除/空值确认（提交 0 星 = 清除评分）；
 * - 补全评分：必须选 1~5 星，未选择时确定按钮禁用。
 * 另外每次打开必须重置星级，否则上一次选过的星会被误带去下一个任务。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import RatingPickerModal from '../RatingPickerModal.vue'

/** a-rate 桩：直接渲染 5 个可点击的星，点击即 emit 对应星数（0 = 清除） */
const RateStub = {
  name: 'ARate',
  props: {
    modelValue: { type: Number, default: 0 },
    count: { type: Number, default: 5 },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  template: `
    <div class="rate-stub">
      <button
        v-for="i in count"
        :key="i"
        class="rate-star"
        :disabled="disabled"
        @click="$emit('update:modelValue', i)"
      >{{ i }}</button>
      <button class="rate-clear" :disabled="disabled" @click="$emit('update:modelValue', 0)">clear</button>
    </div>
  `,
}

const ModalStub = {
  name: 'AModal',
  props: { visible: Boolean, title: String },
  emits: ['cancel'],
  template: `
    <div v-if="visible" class="modal-stub">
      <h3 class="modal-title">{{ title }}</h3>
      <slot />
      <div class="modal-footer"><slot name="footer" /></div>
    </div>
  `,
}

const ButtonStub = {
  name: 'AButton',
  props: { disabled: Boolean, loading: Boolean, type: String },
  emits: ['click'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
}

function mountModal(props: Record<string, unknown> = {}) {
  return mount(RatingPickerModal, {
    props: { visible: true, ...props },
    global: {
      stubs: {
        'a-modal': ModalStub,
        'a-rate': RateStub,
        'a-button': ButtonStub,
      },
    },
  })
}

function buttonByText(wrapper: ReturnType<typeof mountModal>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

describe('RatingPickerModal', () => {
  it('渲染标题与说明；未选星时提示可选择 1~5 星', () => {
    const wrapper = mountModal({
      title: '补全博主素材评分',
      description: '只补未评分素材，已评分的不覆盖。',
    })

    expect(wrapper.find('.modal-title').text()).toBe('补全博主素材评分')
    expect(wrapper.text()).toContain('只补未评分素材')
    expect(wrapper.find('.rating-hint').text()).toBe('请选择 1~5 星')
  })

  it('选择星级后确定按钮可点，confirm 带星级', async () => {
    const wrapper = mountModal({ confirmText: '开始补全', allowClear: false })

    const stars = wrapper.findAll('.rate-star')
    expect(stars.length).toBe(5)
    await stars[2]!.trigger('click')
    expect(wrapper.find('.rating-hint').text()).toBe('已选 3 星')

    const confirm = buttonByText(wrapper, '开始补全')!
    expect(confirm.attributes('disabled')).toBeUndefined()
    await confirm.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[3]])
  })

  it('补全评分场景：未选星时确定按钮禁用，点击也不提交', async () => {
    const wrapper = mountModal({ confirmText: '开始补全' })
    const confirm = buttonByText(wrapper, '开始补全')!

    expect(confirm.attributes('disabled')).toBeDefined()
    await confirm.trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('批量评分场景：允许不清星直接确定（提交 0 = 清除评分）', async () => {
    const wrapper = mountModal({ allowEmptyConfirm: true })
    expect(wrapper.find('.rating-hint').text()).toContain('确定即清除评分')

    await buttonByText(wrapper, '确定')!.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[0]])
  })

  it('allowClear 时提供「清除评分」，提交 0', async () => {
    const wrapper = mountModal({ allowClear: true, allowEmptyConfirm: true })
    await buttonByText(wrapper, '清除评分')!.trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[0]])
  })

  it('取消只关闭弹窗，不提交评分', async () => {
    const wrapper = mountModal()
    await buttonByText(wrapper, '取消')!.trigger('click')

    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
  })

  it('再次打开时重置星级（上一次的选择不带到下一次任务）', async () => {
    const wrapper = mountModal()
    await wrapper.findAll('.rate-star')[4]!.trigger('click')
    expect(wrapper.find('.rating-hint').text()).toBe('已选 5 星')

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    expect(wrapper.find('.rating-hint').text()).toBe('请选择 1~5 星')
  })

  it('loading 时按钮禁用，防止重复提交', async () => {
    const wrapper = mountModal({ loading: true, allowEmptyConfirm: true })
    const confirm = buttonByText(wrapper, '确定')!
    expect(confirm.attributes('disabled')).toBeDefined()
    await confirm.trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })
})
