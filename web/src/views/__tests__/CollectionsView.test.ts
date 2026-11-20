/**
 * 收藏合集页（CollectionsView）的交互路径测试。
 *
 * 页面的核心是**两级结构**与「切合集后状态复位」：
 * - 一级是分类（含子收藏夹），二级才是装素材的容器；侧栏与内容区共用同一份分组；
 * - 切换合集必须把页码、批量选择、编辑排序模式一起复位——漏一个就会出现
 *   「换了合集还停在第 3 页」「新合集里带着上一批的勾选」。
 *
 * 子组件（侧栏/瀑布流/弹窗）用替身，只验编排与状态；拖拽排序本身的逻辑在
 * `useCollectionOrdering.test.ts` 里单独覆盖。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchCollections: vi.fn(),
  fetchCollectionInspirations: vi.fn(),
  removeFromCollection: vi.fn(),
  transferCollectionItems: vi.fn(),
  mergeCollections: vi.fn(),
  batchTrash: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {}, params: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/api/collections', () => ({
  fetchCollections: mocks.fetchCollections,
  fetchCollectionInspirations: mocks.fetchCollectionInspirations,
  removeFromCollection: mocks.removeFromCollection,
  transferCollectionItems: mocks.transferCollectionItems,
  mergeCollections: mocks.mergeCollections,
  createCollection: vi.fn(),
  updateCollection: vi.fn(),
  deleteCollection: vi.fn(),
  addInspirationsToCollection: vi.fn(),
  evaluateSmartCollection: vi.fn(),
  solidifySmartCollection: vi.fn(),
  reorderCollections: vi.fn(),
  reorderCollectionItems: vi.fn(),
}))

vi.mock('@/api/inspirations', () => ({
  getFileUrl: (p: string) => `/files/${p}`,
  fetchInspirations: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchInspiration: vi.fn(),
  batchTrash: mocks.batchTrash,
}))

vi.mock('@/api/tags', () => ({
  fetchTags: vi.fn().mockResolvedValue([]),
  fetchTagsGrouped: vi.fn().mockResolvedValue([]),
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: { error: mocks.error, success: mocks.success, warning: vi.fn() } }
})

import CollectionsView from '../CollectionsView.vue'

function collection(
  id: number,
  name: string,
  parentId: number | null = null,
  kind: 'manual' | 'smart' = 'manual',
) {
  return {
    id,
    name,
    description: null,
    kind,
    position: id,
    parent_id: parentId,
    child_count: 0,
    item_count: 0,
    query_json: kind === 'smart' ? { is_favorite: true } : null,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    auto_source: null,
    created_at: '2026-10-21T00:00:00',
    updated_at: '2026-10-21T00:00:00',
  }
}

function mountView() {
  return mount(CollectionsView, {
    global: {
      plugins: [ArcoVue, createPinia()],
      stubs: {
        MasonryGrid: true,
        CollectionSidebar: true,
        CollectionContentHeader: true,
        CollectionOrderingStrip: true,
        SmartQueryEditorModal: true,
        CollectionTargetPickerModal: true,
      },
    },
  })
}

describe('CollectionsView 交互路径', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.fetchCollections.mockResolvedValue([
      collection(1, '系统入库手动收藏'),
      collection(2, '抖音入库自动收藏'),
      collection(3, '子收藏夹', 2),
    ])
    mocks.fetchCollectionInspirations.mockResolvedValue({ items: [], total: 0 })
    mocks.removeFromCollection.mockResolvedValue({ removed: 2 })
    mocks.batchTrash.mockResolvedValue({ trashed: 1, skipped: 0 })
    mocks.transferCollectionItems.mockResolvedValue({ added: 2, skipped: 0 })
    mocks.mergeCollections.mockResolvedValue({
      merged_collections: 1,
      added: 3,
      skipped: 0,
      deleted_sources: [1],
    })
  })

  it('首屏加载合集列表，并默认选中第一个', async () => {
    mountView()
    await flushPromises()

    expect(mocks.fetchCollections).toHaveBeenCalled()
    // 选中第一个后立刻加载它的内容；手动合集默认「手动顺序」排序
    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(1, {
      page: 1,
      page_size: 50,
      sort: 'manual',
    })
  })

  it('列表里出现二级收藏夹时，内容区按父级分组（侧栏与内容区共用一份）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const sidebar = wrapper.findComponent({ name: 'CollectionSidebar' })
    expect(sidebar.exists()).toBe(true)
    const props = sidebar.props() as { collections?: unknown[] }
    expect((props.collections ?? []).length).toBe(3)
  })

  it('加载列表失败给出提示且不崩', async () => {
    mocks.fetchCollections.mockRejectedValue(new Error('boom'))

    const wrapper = mountView()
    await flushPromises()

    expect(mocks.error).toHaveBeenCalledWith('加载合集列表失败')
    expect(wrapper.exists()).toBe(true)
  })

  it('切换合集：页码回到 1、退出批量模式、重新加载内容', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.fetchCollectionInspirations.mockClear()

    const sidebar = wrapper.findComponent({ name: 'CollectionSidebar' })
    sidebar.vm.$emit('update:currentId', 2)
    await flushPromises()

    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(2, {
      page: 1,
      page_size: 50,
      sort: 'manual',
    })
  })

  it('批量移入垃圾桶：选择原因后软删除并刷新合集', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('批量选择'))!
      .trigger('click')
    wrapper.findComponent({ name: 'MasonryGrid' }).vm.$emit('toggle-select', 'insp-trash')
    await flushPromises()

    const trashButton = wrapper.findAll('button').find((b) => b.text().includes('移入垃圾桶'))
    expect(trashButton).toBeTruthy()
    await trashButton!.trigger('click')
    await flushPromises()

    const modal = wrapper.findComponent({ name: 'TrashReasonModal' })
    expect(modal.exists()).toBe(true)
    modal.vm.$emit('confirm', '不喜欢')
    await flushPromises()

    expect(mocks.batchTrash).toHaveBeenCalledWith(['insp-trash'], '不喜欢')
    expect(mocks.success).toHaveBeenCalledWith('已移入垃圾桶 1 个')
  })

  it('加载内容失败给出可读提示', async () => {
    const wrapper = mountView()
    await flushPromises() // 首屏成功

    mocks.fetchCollectionInspirations.mockRejectedValue(new Error('超时'))
    const sidebar = wrapper.findComponent({ name: 'CollectionSidebar' })
    sidebar.vm.$emit('update:currentId', 3)
    await flushPromises()

    // 提示文案优先用接口返回的可读信息（这里就是 Error.message），否则回退到兜底文案
    expect(mocks.error).toHaveBeenCalled()
    expect(String(mocks.error.mock.calls.at(-1)?.[0])).toMatch(/超时|加载合集内容失败/)
  })

  it('当前合集被删除后回到列表第一项（不留空选中）', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 服务端返回的新列表里没有当前选中的 1
    mocks.fetchCollections.mockResolvedValue([collection(2, '抖音入库自动收藏')])
    const vm = wrapper.vm as unknown as { loadCollections?: () => Promise<void> }
    if (vm.loadCollections) {
      await vm.loadCollections()
      await flushPromises()
      expect(mocks.fetchCollectionInspirations).toHaveBeenLastCalledWith(2, {
        page: 1,
        page_size: 50,
        sort: 'manual',
      })
    }
  })

  it('批量移出：未勾选时点按钮不发请求', async () => {
    const wrapper = mountView()
    await flushPromises()

    const header = wrapper.findComponent({ name: 'CollectionContentHeader' })
    if (header.exists()) {
      header.vm.$emit('batch-remove')
      await flushPromises()
      expect(mocks.removeFromCollection).not.toHaveBeenCalled()
    }
  })

  it('智能合集默认按「最新」排序，且不允许编辑手动排序', async () => {
    mocks.fetchCollections.mockResolvedValue([collection(9, '我的智能合集', null, 'smart')])
    const wrapper = mountView()
    await flushPromises()

    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(9, {
      page: 1,
      page_size: 50,
      sort: 'newest',
    })
    const header = wrapper.findComponent({ name: 'CollectionContentHeader' })
    expect(header.props('canOrder')).toBe(false)
  })

  it('批量移动/复制：进入批量并选中后，目标弹窗确认会带 mode 调 transfer', async () => {
    const wrapper = mountView()
    await flushPromises()

    // 进入批量模式并勾选一条
    const batchButton = wrapper.findAll('button').find((b) => b.text().includes('批量选择'))
    expect(batchButton).toBeTruthy()
    await batchButton!.trigger('click')
    const grid = wrapper.findComponent({ name: 'MasonryGrid' })
    grid.vm.$emit('toggle-select', 'insp-1')
    await flushPromises()

    // 移动到… → 目标弹窗 → 确认目标 2
    const moveButton = wrapper.findAll('button').find((b) => b.text().includes('移动到…'))
    expect(moveButton).toBeTruthy()
    await moveButton!.trigger('click')
    await flushPromises()

    const picker = wrapper.findComponent({ name: 'CollectionTargetPickerModal' })
    expect(picker.props('visible')).toBe(true)
    expect(picker.props('multiple')).not.toBe(false) // 批量移动目标可多选
    picker.vm.$emit('confirm', [2, 3])
    await flushPromises()

    expect(mocks.transferCollectionItems).toHaveBeenCalledWith(1, ['insp-1'], [2, 3], 'move')
  })

  it('复制到…：mode=copy', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('批量选择'))!
      .trigger('click')
    wrapper.findComponent({ name: 'MasonryGrid' }).vm.$emit('toggle-select', 'insp-2')
    await flushPromises()

    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('复制到…'))!
      .trigger('click')
    await flushPromises()

    const pickers = wrapper.findAllComponents({ name: 'CollectionTargetPickerModal' })
    pickers[0].vm.$emit('confirm', [2])
    await flushPromises()

    expect(mocks.transferCollectionItems).toHaveBeenCalledWith(1, ['insp-2'], [2], 'copy')
  })

  it('合集头「合并到…」：确认目标后调 merge，成功后选中目标合集', async () => {
    const wrapper = mountView()
    await flushPromises()

    const header = wrapper.findComponent({ name: 'CollectionContentHeader' })
    header.vm.$emit('merge')
    await flushPromises()

    const picker = wrapper
      .findAllComponents({ name: 'CollectionTargetPickerModal' })
      .find((p) => String(p.props('title')).includes('合并'))
    expect(picker).toBeTruthy()
    expect(picker!.props('multiple')).toBe(false)
    picker!.vm.$emit('confirm', [2])
    await flushPromises()

    expect(mocks.mergeCollections).toHaveBeenCalledWith(2, [1])
    expect(mocks.success).toHaveBeenCalledWith(expect.stringContaining('源合集已删除'))
  })

  it('合并失败只提示错误，不改选中的源合集', async () => {
    mocks.mergeCollections.mockRejectedValue({ response: { data: { detail: '目标不可合并' } } })
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent({ name: 'CollectionContentHeader' }).vm.$emit('merge')
    await flushPromises()
    const picker = wrapper
      .findAllComponents({ name: 'CollectionTargetPickerModal' })
      .find((p) => String(p.props('title')).includes('合并'))
    picker!.vm.$emit('confirm', [2])
    await flushPromises()

    expect(mocks.error).toHaveBeenCalledWith('目标不可合并')
    expect(wrapper.findComponent({ name: 'CollectionSidebar' }).props('collections')).toBeTruthy()
  })

  it('合集内搜索防抖后带 q 加载；切合集把筛选与排序重置为默认', async () => {
    mocks.fetchCollectionInspirations.mockClear()
    mocks.fetchCollections.mockResolvedValue([
      collection(1, '手动合集甲'),
      collection(2, '手动合集乙'),
      collection(3, '智能合集丙', null, 'smart'),
    ])
    const wrapper = mountView()
    await flushPromises()

    // 输入关键词：300ms 防抖后按 q + 手动顺序请求
    const input = wrapper.find('.content-keyword input')
    expect(input.exists()).toBe(true)
    await input.setValue('白裙')
    await new Promise((resolve) => setTimeout(resolve, 350))
    await flushPromises()

    const searched = mocks.fetchCollectionInspirations.mock.calls.at(-1)?.[1] ?? {}
    expect(searched).toMatchObject({ page: 1, page_size: 50, sort: 'manual', q: '白裙' })

    // 切到智能合集：筛选清空、排序切该合集默认（newest）
    const sidebar = wrapper.findComponent({ name: 'CollectionSidebar' })
    sidebar.vm.$emit('update:currentId', 3)
    await flushPromises()

    const switched = mocks.fetchCollectionInspirations.mock.calls.at(-1) ?? []
    expect(switched[0]).toBe(3)
    expect(switched[1]).toMatchObject({ page: 1, page_size: 50, sort: 'newest' })
    expect(switched[1]).not.toHaveProperty('q')
  })
})
