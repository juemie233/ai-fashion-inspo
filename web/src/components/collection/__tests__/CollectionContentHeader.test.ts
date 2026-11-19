/** 系统收藏根只读：不显示编辑条件/转手动/删除（2026-11-12 收藏审查 P1-2）。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import CollectionContentHeader from '../CollectionContentHeader.vue'
import type { CollectionOut } from '@/api/collections'

function collection(over: Partial<CollectionOut> = {}): CollectionOut {
  return {
    id: 1,
    name: '普通智能合集',
    description: null,
    kind: 'smart',
    position: 0,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: null,
    query_json: { is_favorite: true },
    created_at: '',
    updated_at: '',
    ...over,
  }
}

function mountHeader(current: CollectionOut, canOrder = true) {
  return mount(CollectionContentHeader, {
    props: { current, parentName: null, total: 3, ordering: false, canOrder },
    global: { plugins: [ArcoVue] },
  })
}

describe('CollectionContentHeader 系统收藏根', () => {
  it('系统入库手动收藏不显示编辑条件/转手动/删除', () => {
    const wrapper = mountHeader(collection({ name: '系统入库手动收藏' }))
    expect(wrapper.text()).toContain('系统收藏 · 自动同步')
    expect(wrapper.findAll('button').some((b) => b.text().includes('编辑条件'))).toBe(false)
    expect(wrapper.findAll('button').some((b) => b.text().includes('转手动'))).toBe(false)
    expect(wrapper.findAll('button').some((b) => b.text().includes('删除合集'))).toBe(false)
  })

  it('普通智能合集保留编辑/转手动/删除', () => {
    const wrapper = mountHeader(collection())
    expect(wrapper.findAll('button').some((b) => b.text().includes('编辑条件'))).toBe(true)
    expect(wrapper.findAll('button').some((b) => b.text().includes('转手动'))).toBe(true)
    expect(wrapper.findAll('button').some((b) => b.text().includes('删除合集'))).toBe(true)
  })

  it('canOrder=false（按最新/评分等排序）时不显示「编辑排序」', () => {
    const wrapper = mountHeader(collection({ kind: 'manual' }), false)
    expect(wrapper.findAll('button').some((b) => b.text().includes('编辑排序'))).toBe(false)
    // 其它手动合集操作保留
    expect(wrapper.findAll('button').some((b) => b.text().includes('重命名'))).toBe(true)
  })

  it('仅手动、非自动、非系统收藏根显示「合并到…」', () => {
    // 普通手动合集：显示
    const manual = mountHeader(collection({ kind: 'manual' }))
    expect(manual.findAll('button').some((b) => b.text().includes('合并到…'))).toBe(true)

    // 智能合集：不显示
    const smart = mountHeader(collection({ kind: 'smart' }))
    expect(smart.findAll('button').some((b) => b.text().includes('合并到…'))).toBe(false)

    // 抖音自动同步合集：不显示
    const auto = mountHeader(collection({ kind: 'manual', auto_source: 'douyin' }))
    expect(auto.findAll('button').some((b) => b.text().includes('合并到…'))).toBe(false)

    // 系统收藏根：整组操作都不显示
    const system = mountHeader(collection({ kind: 'manual', name: '系统入库手动收藏' }))
    expect(system.findAll('button').some((b) => b.text().includes('合并到…'))).toBe(false)
  })
})
