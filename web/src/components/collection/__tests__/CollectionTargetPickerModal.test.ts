/**
 * 合集目标选择器测试：可选范围规则与单选/多选行为。
 *
 * 规则与「加入合集」选择器一致：智能合集 / 分类节点 / 自动同步合集不可作为目标，
 * 否则用户选完只会拿到后端 400。批量移动/复制可多选（交互上最多 20）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({ fetchCollections: vi.fn() }))

vi.mock('@/api/collections', () => ({
  fetchCollections: mocks.fetchCollections,
}))

import CollectionTargetPickerModal from '../CollectionTargetPickerModal.vue'
import type { CollectionOut } from '@/api/collections'

function collection(over: Partial<CollectionOut> = {}): CollectionOut {
  return {
    id: 1,
    name: '合集',
    description: null,
    kind: 'manual',
    position: 1,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: 0,
    query_json: null,
    created_at: '',
    updated_at: '',
    ...over,
  }
}

function mountPicker(props: Record<string, unknown> = {}) {
  return mount(CollectionTargetPickerModal, {
    props: { visible: true, ...props },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

/** Arco Modal teleport 到 body：直接在 document 上找目标行 */
function optionEls(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('.target-option')]
}

function clickBodyButton(text: string): void {
  const button = [...document.body.querySelectorAll('button')].find((b) =>
    b.textContent?.trim().includes(text),
  )
  expect(button).toBeTruthy()
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.fetchCollections.mockResolvedValue([
    collection({ id: 1, name: '目标甲' }),
    collection({ id: 2, name: '目标乙' }),
    collection({ id: 3, name: '应排除-选中源' }),
    collection({ id: 4, name: '智能合集', kind: 'smart' }),
    collection({ id: 5, name: '分类节点', child_count: 2 }),
    collection({ id: 6, name: '抖音自动合集', auto_source: 'douyin' }),
  ])
})

describe('CollectionTargetPickerModal', () => {
  it('只列手动容器合集，排除智能/分类/自动与 excludeIds', async () => {
    mountPicker({ excludeIds: [3] })
    await flushPromises()

    const names = [...document.querySelectorAll('.target-name')].map((n) => n.textContent)
    expect(names).toEqual(['目标甲', '目标乙'])
  })

  it('多选：勾选多个目标后 confirm 返回全部 id', async () => {
    const wrapper = mountPicker()
    await flushPromises()

    optionEls()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    optionEls()[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(optionEls().filter((el) => el.classList.contains('selected')).length).toBe(2)

    clickBodyButton('确定')
    await flushPromises()
    expect(wrapper.emitted('confirm')?.[0]).toEqual([[1, 2]])
  })

  it('单选（合并）：点第二个目标会替换第一个', async () => {
    const wrapper = mountPicker({ multiple: false, title: '合并到目标合集' })
    await flushPromises()

    optionEls()[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    optionEls()[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(optionEls().filter((el) => el.classList.contains('selected')).length).toBe(1)

    clickBodyButton('确定')
    await flushPromises()
    expect(wrapper.emitted('confirm')?.[0]).toEqual([[2]])
  })
})
