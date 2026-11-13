/** 错误边界：子树渲染异常时展示兜底，重试后恢复。 */

import { defineComponent, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

vi.mock('@/utils/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn() },
  getLastRequestId: () => '',
  copyDiagnostics: vi.fn(async () => true),
}))

import ErrorBoundary from '../ErrorBoundary.vue'

describe('ErrorBoundary', () => {
  it('子组件抛错时显示兜底，重试后恢复子内容', async () => {
    let shouldThrow = true
    const Child = defineComponent({
      name: 'BoomChild',
      setup() {
        return () => {
          if (shouldThrow) throw new Error('渲染炸了')
          return h('div', { class: 'recovered' }, '已恢复')
        }
      },
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    const wrapper = mount(ErrorBoundary, {
      slots: { default: () => h(Child) },
      global: { plugins: [ArcoVue] },
    })
    await nextTick()

    expect(wrapper.text()).toContain('页面渲染出错')
    expect(wrapper.text()).toContain('渲染炸了')

    shouldThrow = false
    const retry = wrapper.findAll('button').find((b) => b.text() === '重试')
    await retry!.trigger('click')
    await nextTick()
    expect(wrapper.find('.recovered').exists()).toBe(true)

    consoleError.mockRestore()
    wrapper.unmount()
  })

  it('正常子树直接透传，不渲染兜底', () => {
    const wrapper = mount(ErrorBoundary, {
      slots: { default: '<div class="ok">正常内容</div>' },
      global: { plugins: [ArcoVue] },
    })

    expect(wrapper.find('.ok').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('页面渲染出错')
    wrapper.unmount()
  })
})
