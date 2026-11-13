/** 加载更多条：按钮文案、加载态与事件。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import LoadMoreBar from '../LoadMoreBar.vue'

function mountBar(props: { loading: boolean; loaded: number; total: number }) {
  return mount(LoadMoreBar, {
    props,
    global: { plugins: [ArcoVue] },
  })
}

describe('LoadMoreBar', () => {
  it('未加载完时显示进度并上抛 load-more', async () => {
    const wrapper = mountBar({ loading: false, loaded: 3, total: 10 })
    const button = wrapper.find('button')

    expect(button.text()).toContain('加载更多（3/10）')
    await button.trigger('click')
    expect(wrapper.emitted('load-more')).toHaveLength(1)
    wrapper.unmount()
  })

  it('加载完（loaded >= total）不渲染', () => {
    const wrapper = mountBar({ loading: false, loaded: 10, total: 10 })
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('loading 透传给按钮', () => {
    const wrapper = mountBar({ loading: true, loaded: 3, total: 10 })
    expect(wrapper.find('button').classes()).toContain('arco-btn-loading')
    wrapper.unmount()
  })
})
