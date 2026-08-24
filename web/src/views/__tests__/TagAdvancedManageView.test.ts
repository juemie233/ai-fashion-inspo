/** 标签高级管理页渲染测试：左侧导航渲染中文标签而非组件对象。 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import TagAdvancedManageView from '@/views/TagAdvancedManageView.vue'

// 路由与 Arco 组件在 jsdom 下可用；面板内部会发请求，这里只验证导航渲染
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

describe('TagAdvancedManageView 左侧导航', () => {
  it('渲染六个中文导航项（不渲染组件对象）', () => {
    const wrapper = mount(TagAdvancedManageView, { global: { stubs: { 'a-button': true } } })
    const navItems = wrapper.findAll('.adv-nav-item')
    expect(navItems).toHaveLength(6)
    const texts = navItems.map((n) => n.text())
    expect(texts).toEqual(['健康度', '聚类', '网络图', '效果分析', '层级树', '历史记录'])
    // 防回归：任何导航项都不应包含组件对象序列化特征
    for (const t of texts) {
      expect(t).not.toContain('__name')
      expect(t).not.toContain('TagHealthPanel')
    }
    wrapper.unmount()
  })
})
