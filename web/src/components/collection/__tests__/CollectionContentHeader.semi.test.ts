/**
 * 合集详情头（`CollectionContentHeader`）半智能/扩充相关补测。
 *
 * 既有 `CollectionContentHeader.test.ts` 覆盖了「按此扩充」按钮的显隐；本文件补：
 * - 半智能徽标文案（智能合集 / 半智能合集）与抖音同步、分类节点的入口裁剪；
 * - 「按此扩充 / 导出 / 删除 / 编辑条件」等按钮真的 emit 了对应事件（父级据此接线弹窗）；
 * - 编辑排序三态（可编辑 / 编辑中 / 不允许编辑）与保存、取消事件的区分。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import CollectionContentHeader from '../CollectionContentHeader.vue'
import type { CollectionOut } from '@/api/collections'

function collection(over: Partial<CollectionOut> = {}): CollectionOut {
  return {
    id: 1,
    name: '通勤穿搭',
    description: null,
    kind: 'manual',
    position: 0,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: 3,
    query_json: null,
    created_at: '',
    updated_at: '',
    ...over,
  }
}

function mountHeader(current: CollectionOut, over: Record<string, unknown> = {}) {
  return mount(CollectionContentHeader, {
    props: {
      current,
      parentName: null,
      total: 3,
      ordering: false,
      canOrder: true,
      ...over,
    },
    global: { plugins: [ArcoVue] },
  })
}

function texts(wrapper: ReturnType<typeof mountHeader>): string[] {
  return wrapper.findAll('button').map((b) => b.text())
}

function findButton(wrapper: ReturnType<typeof mountHeader>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

describe('CollectionContentHeader 半智能与扩充入口', () => {
  it('徽标：普通智能合集显示「智能合集」，半智能显示「半智能合集」，手动合集无徽标', () => {
    expect(mountHeader(collection({ kind: 'smart' })).text()).toContain('智能合集')
    expect(mountHeader(collection({ kind: 'smart' })).text()).not.toContain('半智能合集')

    const semi = mountHeader(collection({ kind: 'smart', semi_manual: true }))
    expect(semi.text()).toContain('半智能合集')

    const manual = mountHeader(collection({ kind: 'manual' }))
    expect(manual.text()).not.toContain('智能合集')
  })

  it('抖音同步合集：显示同步徽标、禁用重命名、隐藏扩充/合并入口', () => {
    const wrapper = mountHeader(collection({ auto_source: 'douyin' }))

    expect(wrapper.text()).toContain('抖音同步')
    const rename = findButton(wrapper, '重命名')
    expect(rename).toBeTruthy()
    expect(rename!.attributes('disabled')).toBeDefined()
    expect(texts(wrapper)).not.toContain('按此扩充')
    expect(texts(wrapper)).not.toContain('合并到…')
  })

  it('分类节点（有子收藏夹）：不给扩充与合并入口，但保留重命名/编辑排序', () => {
    const wrapper = mountHeader(collection({ child_count: 2 }))

    expect(texts(wrapper)).not.toContain('按此扩充')
    expect(texts(wrapper)).not.toContain('合并到…')
    expect(texts(wrapper)).toContain('重命名')
    expect(texts(wrapper)).toContain('编辑排序')
  })

  it('父级面包屑与素材总数', () => {
    const wrapper = mountHeader(collection({ parent_id: 2 }), { parentName: '通勤大类' })

    expect(wrapper.text()).toContain('通勤大类 /')
    expect(wrapper.text()).toContain('共 3 个素材')
  })

  it('「按此扩充」「导出」「删除合集」「编辑条件」分别 emit 对应事件', async () => {
    const manual = mountHeader(collection({ kind: 'manual' }))
    await findButton(manual, '按此扩充')!.trigger('click')
    await findButton(manual, '导出')!.trigger('click')
    await findButton(manual, '删除合集')!.trigger('click')

    expect(manual.emitted('expand')).toHaveLength(1)
    expect(manual.emitted('export')).toHaveLength(1)
    expect(manual.emitted('delete')).toHaveLength(1)

    const smart = mountHeader(collection({ kind: 'smart' }))
    await findButton(smart, '编辑条件')!.trigger('click')
    expect(smart.emitted('edit-smart')).toHaveLength(1)
  })

  it('编辑排序三态：可编辑 → 编辑中（保存/取消）→ canOrder=false 时三者都不显示', async () => {
    const idle = mountHeader(collection(), { ordering: false, canOrder: true })
    expect(texts(idle)).toContain('编辑排序')
    expect(texts(idle)).not.toContain('保存顺序')
    await findButton(idle, '编辑排序')!.trigger('click')
    expect(idle.emitted('enter-ordering')).toHaveLength(1)

    const editing = mountHeader(collection(), { ordering: true, canOrder: true })
    expect(texts(editing)).not.toContain('编辑排序')
    await findButton(editing, '保存顺序')!.trigger('click')
    await findButton(editing, '取消排序')!.trigger('click')
    expect(editing.emitted('save-order')).toHaveLength(1)
    expect(editing.emitted('cancel-ordering')).toHaveLength(1)

    const notOrderable = mountHeader(collection(), { ordering: false, canOrder: false })
    expect(texts(notOrderable)).not.toContain('编辑排序')
    expect(texts(notOrderable)).not.toContain('保存顺序')
    expect(texts(notOrderable)).not.toContain('取消排序')
  })

  it('系统收藏根只读：导出保留、AI 整理/扩充/删除都不显示', () => {
    const wrapper = mountHeader(
      collection({ kind: 'manual', name: '系统入库手动收藏', parent_id: null }),
    )

    expect(texts(wrapper)).toContain('导出')
    expect(texts(wrapper)).not.toContain('AI 整理')
    expect(texts(wrapper)).not.toContain('按此扩充')
    expect(texts(wrapper)).not.toContain('删除合集')
  })
})
