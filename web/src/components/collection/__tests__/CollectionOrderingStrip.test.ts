/**
 * 合集排序条带（CollectionOrderingStrip）：缩略图顺序编辑的展示与回抛测试。
 *
 * 该组件从 CollectionsView 抽出（无独立单测，覆盖率 0%），本身不持有顺序：
 * - 缩略图优先用 thumbnail_path，没有才退回 file_path（getFileUrl 做 URL 编码）；
 * - 首项禁用「上移」、末项禁用「下移」，避免把手动顺序移出边界；
 * - 拖拽与 ↑↓ 都只把「起点/目标 ID + 方向」回抛给父组件的排序 composable，
 *   真正的重排和保存不在组件内。事件名/参数写错会导致「拖了没反应」或保存错位。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import type { InspirationOut } from '@/api/inspirations'
import CollectionOrderingStrip from '../CollectionOrderingStrip.vue'

function makeInspiration(overrides: Pick<InspirationOut, 'id'> & Partial<InspirationOut>) {
  return {
    file_path: `storage/images/${overrides.id}.jpg`,
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-11-01T00:00:00Z',
    tags: [],
    ...overrides,
  } as InspirationOut
}

const ITEMS: InspirationOut[] = [
  makeInspiration({ id: 'a', thumbnail_path: 'thumbs/a b.jpg' }),
  makeInspiration({ id: 'b' }),
  makeInspiration({ id: 'c', thumbnail_path: 'thumbs/c.jpg' }),
]

function mountStrip(items: InspirationOut[] = ITEMS) {
  return mount(CollectionOrderingStrip, {
    props: { items },
    global: { plugins: [ArcoVue] },
  })
}

type StripWrapper = ReturnType<typeof mountStrip>

function cards(wrapper: StripWrapper) {
  return wrapper.findAll('.ordering-card')
}

function moveButton(wrapper: StripWrapper, index: number, label: '上移素材' | '下移素材') {
  return cards(wrapper)[index].find(`[aria-label="${label}"]`)
}

describe('CollectionOrderingStrip 缩略图与提示', () => {
  it('渲染排序提示与每一条素材的缩略图', () => {
    const wrapper = mountStrip()

    expect(wrapper.text()).toContain('拖动缩略图调整展示顺序')
    expect(cards(wrapper)).toHaveLength(3)
    expect(cards(wrapper)[0].attributes('draggable')).toBe('true')
  })

  it('缩略图优先取 thumbnail_path，缺失时退回 file_path，并对路径分段做 URL 编码', () => {
    const wrapper = mountStrip()

    const sources = wrapper.findAll('.ordering-card img').map((img) => img.attributes('src'))
    expect(sources).toEqual([
      '/api/files/thumbs/a%20b.jpg', // thumbnail_path 优先
      '/api/files/storage/images/b.jpg', // 没有 thumbnail 用 file_path
      '/api/files/thumbs/c.jpg',
    ])
  })
})

describe('CollectionOrderingStrip 边界与按钮', () => {
  it('首项禁用上移、末项禁用下移，其它按钮可用', () => {
    const wrapper = mountStrip()

    expect(moveButton(wrapper, 0, '上移素材').classes()).toContain('arco-btn-disabled')
    expect(moveButton(wrapper, 0, '下移素材').classes()).not.toContain('arco-btn-disabled')
    expect(moveButton(wrapper, 1, '上移素材').classes()).not.toContain('arco-btn-disabled')
    expect(moveButton(wrapper, 1, '下移素材').classes()).not.toContain('arco-btn-disabled')
    expect(moveButton(wrapper, 2, '下移素材').classes()).toContain('arco-btn-disabled')
  })

  it('点击 ↑↓ 回抛 move(id, direction)', async () => {
    const wrapper = mountStrip()

    await moveButton(wrapper, 0, '下移素材').trigger('click')
    await moveButton(wrapper, 2, '上移素材').trigger('click')

    expect(wrapper.emitted('move')).toEqual([
      ['a', 'down'],
      ['c', 'up'],
    ])
  })

  it('只有一条素材时上下移都禁用', () => {
    const wrapper = mountStrip([makeInspiration({ id: 'only' })])

    expect(moveButton(wrapper, 0, '上移素材').classes()).toContain('arco-btn-disabled')
    expect(moveButton(wrapper, 0, '下移素材').classes()).toContain('arco-btn-disabled')
  })
})

describe('CollectionOrderingStrip 拖拽回抛', () => {
  it('dragstart 回抛起点 ID，drop 回抛目标 ID', async () => {
    const wrapper = mountStrip()

    await cards(wrapper)[0].trigger('dragstart')
    await cards(wrapper)[2].trigger('drop')

    expect(wrapper.emitted('drag-start')).toEqual([['a']])
    expect(wrapper.emitted('drop')).toEqual([['c']])
  })

  it('空列表只渲染提示、不渲染缩略图', () => {
    const wrapper = mountStrip([])

    expect(cards(wrapper)).toHaveLength(0)
    expect(wrapper.text()).toContain('拖动缩略图调整展示顺序')
    expect(wrapper.emitted('drag-start')).toBeUndefined()
  })
})
