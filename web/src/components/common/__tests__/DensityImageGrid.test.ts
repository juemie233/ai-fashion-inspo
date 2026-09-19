/**
 * DensityImageGrid 测试：默认「自适应列数」，传入 columns 后按档位固定列数。
 *
 * 固定列数是为 f2 结果面板这类「图片要看大一点」的场景加的（紧凑 6 / 标准 4 / 宽松 3），
 * 关键在于单元轨道用 minmax(0, 1fr)——允许收缩，图片不会把网格顶宽出横向滚动条。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 按钮桩，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DensityImageGrid from '../DensityImageGrid.vue'

const buttonGroupStub = defineComponent({
  name: 'AButtonGroup',
  render() {
    return h('div', { class: 'btn-group' }, this.$slots.default?.())
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  // 不声明 emits：父组件的 @click 直接落到根元素上（属性透传）
  render() {
    return h('button', this.$slots.default?.())
  },
})

function mountGrid(props: Record<string, unknown> = {}) {
  return mount(DensityImageGrid, {
    props,
    global: { stubs: { 'a-button-group': buttonGroupStub, 'a-button': buttonStub } },
  })
}

function styleOf(wrapper: ReturnType<typeof mountGrid>): string {
  return wrapper.find('.density-grid-body').attributes('style') ?? ''
}

describe('DensityImageGrid', () => {
  it('不传 columns：只按密度类走 auto-fill 自适应，不写内联列数', () => {
    const w = mountGrid({ density: 'standard' })

    expect(w.find('.density-grid-body').classes()).toContain('density-standard')
    expect(styleOf(w)).not.toContain('grid-template-columns')
  })

  it('传 columns：每档按固定列数渲染（0 最小宽保证可收缩）', async () => {
    const w = mountGrid({
      density: 'comfortable',
      columns: { compact: 6, standard: 4, comfortable: 3 },
    })
    expect(styleOf(w)).toContain('grid-template-columns: repeat(3, minmax(0, 1fr))')

    await w.setProps({ density: 'compact' })
    expect(styleOf(w)).toContain('repeat(6, minmax(0, 1fr))')

    await w.setProps({ density: 'standard' })
    expect(styleOf(w)).toContain('repeat(4, minmax(0, 1fr))')
  })

  it('密度切换按钮把选择 emit 给父组件（v-model）', async () => {
    const w = mountGrid({ density: 'standard' })

    const buttons = w.findAll('button')
    expect(buttons.map((b) => b.text())).toEqual(['紧凑', '标准', '宽松'])

    await buttons[2].trigger('click')
    expect(w.emitted('update:density')?.[0]).toEqual(['comfortable'])
  })

  it('showSwitch=false：只保留左侧工具栏插槽，不渲染密度按钮', () => {
    const w = mount(DensityImageGrid, {
      props: { showSwitch: false },
      slots: { 'header-left': '<span class="mine">自定义</span>' },
      global: { stubs: { 'a-button-group': buttonGroupStub, 'a-button': buttonStub } },
    })

    expect(w.find('.mine').exists()).toBe(true)
    expect(w.findAll('button')).toHaveLength(0)
  })
})
