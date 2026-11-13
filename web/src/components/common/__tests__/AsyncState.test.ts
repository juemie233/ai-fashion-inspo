/** 异步状态容器：四态优先级、空态文案与重试事件。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import AsyncState from '../AsyncState.vue'

function mountState(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  return mount(AsyncState, {
    props,
    slots: { default: '<div class="content">内容</div>', ...slots },
    global: { plugins: [ArcoVue] },
  })
}

describe('AsyncState', () => {
  it('loading 优先展示加载态，不渲染内容', () => {
    const wrapper = mountState({ loading: true, error: '错误', empty: true })
    expect(wrapper.text()).toContain('加载中...')
    expect(wrapper.find('.content').exists()).toBe(false)
    wrapper.unmount()
  })

  it('无 loading 时展示错误与重试按钮，点击上抛 retry', async () => {
    const wrapper = mountState({ error: '请求失败' })
    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('请求失败')

    const retry = wrapper.findAll('button').find((b) => b.text() === '重试')
    await retry!.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
    wrapper.unmount()
  })

  it('空态使用传入文案，不展示默认插槽', () => {
    const wrapper = mountState({ empty: true, emptyText: '暂无记录' })
    expect(wrapper.text()).toContain('暂无记录')
    expect(wrapper.find('.content').exists()).toBe(false)
    wrapper.unmount()
  })

  it('正常状态渲染默认插槽', () => {
    const wrapper = mountState()
    expect(wrapper.find('.content').exists()).toBe(true)
    wrapper.unmount()
  })
})
