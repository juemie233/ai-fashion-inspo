/** 标签高级管理页渲染测试：左侧导航渲染中文标签而非组件对象。 */

import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TagAdvancedManageView from '@/views/TagAdvancedManageView.vue'
import { scanHealth } from '@/api/tagAdvanced'

// 路由与 Arco 组件在 jsdom 下可用；面板内部会发请求，这里只验证导航渲染
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

// 进入页面不应自动创建健康度扫描任务：仅校验提交入口是否被调用，避免测试真发网络请求
vi.mock('@/api/tagAdvanced', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/tagAdvanced')>()
  return { ...actual, scanHealth: vi.fn() }
})

describe('TagAdvancedManageView 左侧导航', () => {
  it('渲染七个中文导航项（不渲染组件对象）', () => {
    const wrapper = mount(TagAdvancedManageView, { global: { stubs: { 'a-button': true } } })
    const navItems = wrapper.findAll('.adv-nav-item')
    expect(navItems).toHaveLength(7)
    const texts = navItems.map((n) => n.text())
    expect(texts).toEqual([
      '健康度',
      '聚类',
      '网络图',
      '效果分析',
      '层级树',
      '历史记录',
      '标签治理',
    ])
    // 防回归：任何导航项都不应包含组件对象序列化特征
    for (const t of texts) {
      expect(t).not.toContain('__name')
      expect(t).not.toContain('TagHealthPanel')
    }
    wrapper.unmount()
  })

  it('进入页面不自动创建健康度扫描任务（只允许手动触发）', async () => {
    const wrapper = mount(TagAdvancedManageView, { global: { stubs: { 'a-button': true } } })
    await flushPromises()
    expect(vi.mocked(scanHealth)).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
