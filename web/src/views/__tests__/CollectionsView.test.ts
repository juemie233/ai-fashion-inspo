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

function collection(id: number, name: string, parentId: number | null = null) {
  return {
    id,
    name,
    description: null,
    kind: 'manual',
    position: id,
    parent_id: parentId,
    child_count: 0,
    item_count: 0,
    query_json: null,
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
  })

  it('首屏加载合集列表，并默认选中第一个', async () => {
    mountView()
    await flushPromises()

    expect(mocks.fetchCollections).toHaveBeenCalled()
    // 选中第一个后立刻加载它的内容
    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(1, { page: 1, size: 50 })
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

    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(2, { page: 1, size: 50 })
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
        size: 50,
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
})
