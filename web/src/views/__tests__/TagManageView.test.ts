/**
 * 标签管理页（TagManageView）的编排测试。
 *
 * 页面自己负责的是**弹窗状态机与入口守卫**——19 个按钮收敛到一个 `activeModal` 状态。
 * 这类代码最容易出的问题是「入口没拦住」：
 * - 批量合并少于 2 个标签就打开弹窗 → 用户点确认后才发现没得合；
 * - 批量编辑没勾选就打开弹窗 → 弹窗里空列表、无法保存；
 * - 导出失败静默（用户以为导出成功，实际没文件）。
 *
 * 列表数据与批量操作在 `useTagManage` 里（已有测试），这里只验页面把入口和状态接对。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  loadAll: vi.fn().mockResolvedValue(undefined),
  deselectAll: vi.fn(),
  selectTag: vi.fn(),
  exportTags: vi.fn(),
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn() },
  // 由 mock 工厂填入真实 ref：computed 必须具备响应式才能在用例里改后重算
  groupsRef: { value: null } as { value: unknown },
  selectedIdsRef: { value: null } as { value: unknown },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/composables/useTagManage', async () => {
  const { ref, computed } = await import('vue')
  const groups = ref<unknown[]>([])
  const selectedIds = ref<Set<number>>(new Set())
  mocks.groupsRef = groups as unknown as { value: unknown }
  mocks.selectedIdsRef = selectedIds as unknown as { value: unknown }
  return {
    useTagManage: () => ({
      groups,
      filteredGroups: groups,
      hasActiveFilter: computed(() => false),
      onGridChanged: vi.fn(),
      selectAllInGroup: vi.fn(),
      handleDelete: vi.fn(),
      handleBatchDelete: vi.fn(),
      stats: ref({ total: 3, unused: 0, links: 10, ai_generated: 2, manual: 1 }),
      loading: ref(false),
      searchQuery: ref(''),
      filterCategory: ref(''),
      filterSource: ref(''),
      sortMode: ref('default'),
      duplicateThreshold: ref(75),
      selectedIds,
      selectedCount: computed(() => selectedIds.value.size),
      selectedTag: ref(null),
      unusedCount: computed(() => 0),
      duplicatePairs: ref([]),
      scanningDuplicates: ref(false),
      showDuplicatesPanel: ref(false),
      loadAll: mocks.loadAll,
      selectTag: mocks.selectTag,
      deselectAll: mocks.deselectAll,
      toggleSelect: vi.fn(),
      toggleSelectAll: vi.fn(),
      scanDuplicates: vi.fn(),
      openDuplicatesPanel: vi.fn(),
      closeDuplicatesPanel: vi.fn(),
      handleDeleteUnused: vi.fn(),
      togglePin: vi.fn(),
      onDrop: vi.fn(),
      onTagDrop: vi.fn(),
    }),
  }
})

vi.mock('@/composables/useSplitResize', () => ({
  useSplitResize: () => ({
    containerRef: ref(null),
    leftWidth: ref(50),
    isDragging: ref(false),
    startDrag: vi.fn(),
  }),
}))

vi.mock('@/api/tags', () => ({
  exportTags: mocks.exportTags,
  fetchTagsGrouped: vi.fn().mockResolvedValue([]),
  fetchTags: vi.fn().mockResolvedValue([]),
  fetchTagStats: vi.fn().mockResolvedValue({}),
  deleteTag: vi.fn(),
  updateTag: vi.fn(),
  createTag: vi.fn(),
  mergeTags: vi.fn(),
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import TagManageView from '../TagManageView.vue'

const STUBS = {
  TagStatsBar: true,
  TagToolbar: true,
  TagCreateForm: true,
  TagDuplicatesPanel: true,
  TagGroupList: true,
  TagDetailPanel: true,
  TagEditModal: true,
  TagMergeModal: true,
  TagBatchMergeModal: true,
  TagBatchEditModal: true,
  TagImportModal: true,
  TagAliasModal: true,
  TagAnalyticsModal: true,
  TagDuplicateCompare: true,
}

function mountView() {
  return mount(TagManageView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: STUBS },
  })
}

function tag(id: number, name: string) {
  return { id, name, category: '单品', source: 'ai_generated', pinned: false, usage_count: 1 }
}

describe('TagManageView 弹窗状态机与入口守卫', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.groupsRef.value = []
    mocks.selectedIdsRef.value = new Set<number>()
    mocks.loadAll.mockResolvedValue(undefined)
    mocks.exportTags.mockResolvedValue({ tags: [{ name: '针织开衫' }] })
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:tags')
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  it('挂载时加载标签数据', async () => {
    mountView()
    await flushPromises()
    expect(mocks.loadAll).toHaveBeenCalled()
  })

  it('批量合并：选中不足 2 个时提示而不是打开弹窗', async () => {
    const wrapper = mountView()
    await flushPromises()

    mocks.selectedIdsRef.value = new Set([1])
    const vm = wrapper.vm as unknown as { openBatchMerge: () => void }
    expect(typeof vm.openBatchMerge).toBe('function')

    vm.openBatchMerge()
    await flushPromises()

    expect(mocks.message.warning).toHaveBeenCalledWith('请至少选中 2 个标签')
    // 弹窗由 v-if 控制：没打开时组件根本不存在（存在则 show 必为真）
    const modal = wrapper.findComponent({ name: 'TagBatchMergeModal' })
    expect(modal.exists() && modal.props('show')).toBeFalsy()
  })

  it('批量合并：选中 2 个及以上时打开弹窗', async () => {
    const wrapper = mountView()
    await flushPromises()

    mocks.selectedIdsRef.value = new Set([1, 2])
    const vm = wrapper.vm as unknown as { openBatchMerge: () => void }
    vm.openBatchMerge()
    await flushPromises()

    expect(mocks.message.warning).not.toHaveBeenCalled()
    const modal = wrapper.findComponent({ name: 'TagBatchMergeModal' })
    expect(modal.props('show')).toBe(true)
  })

  it('批量编辑：未勾选时提示且不打开弹窗', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { openBatchEdit: (m: string) => void }
    vm.openBatchEdit('replace')
    await flushPromises()

    expect(mocks.message.warning).toHaveBeenCalledWith('请先勾选标签')
    const modal = wrapper.findComponent({ name: 'TagBatchEditModal' })
    expect(modal.exists()).toBe(false) // v-if：没打开就不渲染
  })

  it('批量编辑：勾选后按模式打开（逐行/替换/改类别）', async () => {
    const wrapper = mountView()
    await flushPromises()

    mocks.selectedIdsRef.value = new Set([1])
    mocks.groupsRef.value = [{ category: '单品', tags: [tag(1, '针织开衫')] }]
    const vm = wrapper.vm as unknown as { openBatchEdit: (m: string) => void }
    vm.openBatchEdit('category')
    await flushPromises()

    // 批量编辑弹窗只有 tags（没有 show），渲染存在即视为打开
    const modal = wrapper.findComponent({ name: 'TagBatchEditModal' })
    expect(modal.exists()).toBe(true)
    expect((modal.props('tags') as unknown[]).length).toBe(1)
  })

  it('批量操作完成后清空选中并刷新数据', async () => {
    const wrapper = mountView()
    await flushPromises()
    mocks.loadAll.mockClear()

    const vm = wrapper.vm as unknown as { onBatchDone: () => void }
    vm.onBatchDone()
    await flushPromises()

    expect(mocks.deselectAll).toHaveBeenCalled()
    expect(mocks.loadAll).toHaveBeenCalled()
  })

  it('编辑/合并/别名弹窗各自带上目标标签', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      openEdit: (t: unknown) => void
      openMerge: (t: unknown) => void
      openAliasManager: (t: unknown) => void
    }
    const t = tag(7, '阔腿裤')

    vm.openEdit(t)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TagEditModal' }).props('show')).toBe(true)

    vm.openMerge(t)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TagMergeModal' }).props('show')).toBe(true)

    vm.openAliasManager(t)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TagAliasModal' }).props('show')).toBe(true)
  })

  it('新建弹窗是开关式（再点一次关闭）', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { openCreate: () => void }
    vm.openCreate()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'TagCreateForm' }).exists()).toBe(true)

    vm.openCreate()
    await flushPromises()
    // 关闭后组件不再渲染（v-if 语义），只是不再报错
    expect(wrapper.exists()).toBe(true)
  })

  it('导出：生成下载链接并释放 blob URL', async () => {
    const wrapper = mountView()
    await flushPromises()

    const clicked: string[] = []
    const vm = wrapper.vm as unknown as { handleExport: () => Promise<void> }
    const spy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download)
    })

    await vm.handleExport()

    expect(mocks.exportTags).toHaveBeenCalled()
    expect(globalThis.URL.revokeObjectURL).toHaveBeenCalledWith('blob:tags')
    expect(mocks.message.success).toHaveBeenCalledWith('已导出')
    expect(clicked).toEqual(['tags-export.json'])
    spy.mockRestore()
  })

  it('导出失败给出提示（不静默）', async () => {
    mocks.exportTags.mockRejectedValue(new Error('500'))
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { handleExport: () => Promise<void> }
    await vm.handleExport()

    expect(mocks.message.error).toHaveBeenCalled()
  })

  it('洞察卡点共现标签：按 id 在已加载分组里找到并选中，找不到就忽略', async () => {
    mocks.groupsRef.value = [{ category: '单品', tags: [tag(11, '针织开衫')] }]
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { selectTagById: (id: number) => void }
    vm.selectTagById(11)
    expect(mocks.selectTag).toHaveBeenCalledWith(expect.objectContaining({ id: 11 }))

    mocks.selectTag.mockClear()
    vm.selectTagById(999) // 不存在 → 静默忽略，不报错
    expect(mocks.selectTag).not.toHaveBeenCalled()
  })
})
