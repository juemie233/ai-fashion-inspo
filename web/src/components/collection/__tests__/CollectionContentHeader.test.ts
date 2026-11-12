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

function mountHeader(current: CollectionOut) {
  return mount(CollectionContentHeader, {
    props: { current, parentName: null, total: 3, ordering: false },
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
})
