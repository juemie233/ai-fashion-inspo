/**
 * 收藏合集页「半智能合集 / 按此扩充 / 撤销批次」编排测试（2026-11-22 新功能）。
 *
 * 审查修复（34ecbfd）后的口径：
 * - 「按此扩充」入口对手动与智能合集都开放；纯智能合集**打开弹窗时不做任何转换**，
 *   转换延后到弹窗里真正点「加入」时（取消不留副作用），所以视图层不应弹确认框、
 *   也不应调用 convertCollectionToSemi；
 * - 扩充成功（弹窗 emit applied）后，只有**当前合集**才挂顶部撤销条，并刷新内容与列表；
 * - 撤销调 undoCollectionExpansion(合集 id, batchId)，成功后撤条消失；失败保留撤条可重试；
 *   撤销中防重复点击；
 * - 半智能合集可以装素材（显示「添加素材 / 批量选择」工具条），纯智能合集不可以；
 * - 切合集时撤销条不跨合集显示、扩充弹窗自动关闭。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchCollections: vi.fn(),
  fetchCollectionInspirations: vi.fn(),
  removeFromCollection: vi.fn(),
  transferCollectionItems: vi.fn(),
  mergeCollections: vi.fn(),
  undoCollectionExpansion: vi.fn(),
  convertCollectionToSemi: vi.fn(),
  batchTrash: vi.fn(),
  toggleFavorite: vi.fn(),
  modalConfirm: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
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
  undoCollectionExpansion: mocks.undoCollectionExpansion,
  convertCollectionToSemi: mocks.convertCollectionToSemi,
  createCollection: vi.fn(),
  updateCollection: vi.fn(),
  deleteCollection: vi.fn(),
  solidifyCollection: vi.fn(),
  addToCollection: vi.fn(),
  SYSTEM_MANUAL_COLLECTION_NAME: '系统入库手动收藏',
}))

vi.mock('@/api/inspirations', () => ({
  getFileUrl: (p: string) => `/files/${p}`,
  fetchInspirations: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  fetchInspiration: vi.fn(),
  batchTrash: mocks.batchTrash,
  toggleFavorite: mocks.toggleFavorite,
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
  return {
    ...actual,
    Message: mocks.message,
    Modal: { confirm: mocks.modalConfirm },
  }
})

import CollectionsView from '../CollectionsView.vue'

/** 扩充弹窗替身：只暴露 visible / collection，供用例断言接线与触发 applied。 */
const ExpandStub = defineComponent({
  name: 'CollectionExpandModal',
  props: {
    visible: { type: Boolean, default: false },
    collection: { type: Object, default: null },
  },
  emits: ['update:visible', 'applied'],
  template: '<div data-test="expand-stub" />',
})

const STUBS = {
  MasonryGrid: true,
  CollectionSidebar: true,
  CollectionContentHeader: true,
  CollectionOrderingStrip: true,
  SmartQueryEditorModal: true,
  CollectionTargetPickerModal: true,
  CollectionExportModal: true,
  CollectionAiModal: true,
  CollectionExpandModal: ExpandStub,
  TrashReasonModal: true,
}

interface CollectionLike {
  id: number
  name: string
  kind: 'manual' | 'smart'
  semi_manual?: boolean
  [key: string]: unknown
}

function collection(
  id: number,
  name: string,
  kind: 'manual' | 'smart' = 'manual',
  extra: Record<string, unknown> = {},
): CollectionLike {
  return {
    id,
    name,
    description: null,
    kind,
    position: id,
    parent_id: null,
    child_count: 0,
    item_count: 0,
    query_json: kind === 'smart' ? { is_favorite: true } : null,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    auto_source: null,
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z',
    ...extra,
  }
}

function mountView() {
  return mount(CollectionsView, {
    global: { plugins: [ArcoVue, createPinia()], stubs: STUBS },
  })
}

type ViewWrapper = ReturnType<typeof mountView>

function headerOf(wrapper: ViewWrapper) {
  return wrapper.findComponent({ name: 'CollectionContentHeader' })
}

function expandModalOf(wrapper: ViewWrapper) {
  return wrapper.findComponent({ name: 'CollectionExpandModal' })
}

function sidebarOf(wrapper: ViewWrapper) {
  return wrapper.findComponent({ name: 'CollectionSidebar' })
}

function buttonWithText(wrapper: ViewWrapper, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

/** 通过侧栏 v-model 事件切换当前合集（等价于用户点击侧栏） */
async function switchCollection(wrapper: ViewWrapper, id: number) {
  sidebarOf(wrapper).vm.$emit('update:currentId', id)
  await flushPromises()
}

/** 触发一次「按此扩充」入口（由详情头 emit，视图接线到弹窗） */
async function clickExpand(wrapper: ViewWrapper) {
  headerOf(wrapper).vm.$emit('expand')
  await flushPromises()
}

/** 模拟扩充弹窗成功回调：父级据此挂撤销条 */
async function emitApplied(
  wrapper: ViewWrapper,
  payload: { collectionId: number; batch_id: string; added: number },
) {
  expandModalOf(wrapper).vm.$emit('applied', payload)
  await flushPromises()
}

function expandBannerText(wrapper: ViewWrapper): string {
  return wrapper.text()
}

describe('CollectionsView 半智能 / 按此扩充', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mocks.fetchCollections.mockResolvedValue([
      collection(1, '手动 A'),
      collection(2, '手动 B'),
      collection(3, '纯智能 C', 'smart'),
      collection(4, '半智能 D', 'smart', { semi_manual: true }),
    ])
    mocks.fetchCollectionInspirations.mockResolvedValue({ items: [], total: 0 })
    mocks.undoCollectionExpansion.mockResolvedValue({ removed: 2, batch_id: 'b1' })
  })

  it('手动合集点「按此扩充」：直接打开弹窗，不弹转换确认', async () => {
    const wrapper = mountView()
    await flushPromises()

    await clickExpand(wrapper)

    expect(expandModalOf(wrapper).props('visible')).toBe(true)
    expect((expandModalOf(wrapper).props('collection') as CollectionLike).id).toBe(1)
    expect(mocks.modalConfirm).not.toHaveBeenCalled()
    expect(mocks.convertCollectionToSemi).not.toHaveBeenCalled()
  })

  it('纯智能合集也直接打开弹窗：视图层不做转换（取消不留副作用）', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchCollection(wrapper, 3)

    await clickExpand(wrapper)

    const modal = expandModalOf(wrapper)
    expect(modal.props('visible')).toBe(true)
    const passed = modal.props('collection') as CollectionLike
    expect(passed.kind).toBe('smart')
    expect(passed.semi_manual).toBeFalsy()
    expect(mocks.modalConfirm).not.toHaveBeenCalled()
    expect(mocks.convertCollectionToSemi).not.toHaveBeenCalled()
  })

  it('扩充成功：显示撤销条，并刷新合集内容与列表', async () => {
    const wrapper = mountView()
    await flushPromises()
    const contentCalls = mocks.fetchCollectionInspirations.mock.calls.length
    const listCalls = mocks.fetchCollections.mock.calls.length

    await emitApplied(wrapper, { collectionId: 1, batch_id: 'b1', added: 3 })

    expect(expandBannerText(wrapper)).toContain('本次扩充已加入 3 个素材')
    expect(buttonWithText(wrapper, '撤销本次扩充')).toBeTruthy()
    expect(mocks.fetchCollectionInspirations.mock.calls.length).toBeGreaterThan(contentCalls)
    expect(mocks.fetchCollections.mock.calls.length).toBeGreaterThan(listCalls)
  })

  it('扩充回调不是当前合集：不挂撤销条（但仍刷新数据）', async () => {
    const wrapper = mountView()
    await flushPromises()
    const listCalls = mocks.fetchCollections.mock.calls.length

    await emitApplied(wrapper, { collectionId: 2, batch_id: 'b-other', added: 1 })

    expect(expandBannerText(wrapper)).not.toContain('本次扩充已加入')
    expect(mocks.fetchCollections.mock.calls.length).toBeGreaterThan(listCalls)
  })

  it('撤销成功：调 undoCollectionExpansion(id, batchId)、提示移除数、撤销条消失并刷新', async () => {
    const wrapper = mountView()
    await flushPromises()
    await emitApplied(wrapper, { collectionId: 1, batch_id: 'b1', added: 3 })
    const contentCalls = mocks.fetchCollectionInspirations.mock.calls.length

    await buttonWithText(wrapper, '撤销本次扩充')!.trigger('click')
    await flushPromises()

    expect(mocks.undoCollectionExpansion).toHaveBeenCalledWith(1, 'b1')
    expect(mocks.message.success).toHaveBeenCalledWith('已撤销本次扩充（移除 2 个素材）')
    expect(expandBannerText(wrapper)).not.toContain('本次扩充已加入')
    expect(mocks.fetchCollectionInspirations.mock.calls.length).toBeGreaterThan(contentCalls)
  })

  it('撤销失败：透出后端 detail，撤销条保留以便重试', async () => {
    mocks.undoCollectionExpansion.mockRejectedValue({
      response: { data: { detail: '批次已过期' } },
    })
    const wrapper = mountView()
    await flushPromises()
    await emitApplied(wrapper, { collectionId: 1, batch_id: 'b1', added: 3 })

    await buttonWithText(wrapper, '撤销本次扩充')!.trigger('click')
    await flushPromises()

    expect(mocks.message.error).toHaveBeenCalledWith('批次已过期')
    expect(expandBannerText(wrapper)).toContain('本次扩充已加入 3 个素材')
  })

  it('撤销进行中防重复点击：只发一次请求', async () => {
    let resolveUndo: (value: { removed: number; batch_id: string }) => void = () => {}
    mocks.undoCollectionExpansion.mockReturnValue(
      new Promise((resolve) => {
        resolveUndo = resolve
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    await emitApplied(wrapper, { collectionId: 1, batch_id: 'b1', added: 3 })

    const undoBtn = buttonWithText(wrapper, '撤销本次扩充')!
    await undoBtn.trigger('click')
    await undoBtn.trigger('click')

    expect(mocks.undoCollectionExpansion).toHaveBeenCalledTimes(1)
    const vm = wrapper.vm as unknown as { undoingExpansion: boolean }
    expect(vm.undoingExpansion).toBe(true)

    resolveUndo({ removed: 2, batch_id: 'b1' })
    await flushPromises()
    expect(vm.undoingExpansion).toBe(false)
  })

  it('切合集：撤销条不跨合集显示，扩充弹窗自动关闭', async () => {
    const wrapper = mountView()
    await flushPromises()
    await emitApplied(wrapper, { collectionId: 1, batch_id: 'b1', added: 3 })
    expect(expandBannerText(wrapper)).toContain('本次扩充已加入 3 个素材')

    await switchCollection(wrapper, 2)

    expect(expandBannerText(wrapper)).not.toContain('本次扩充已加入')
    expect(expandModalOf(wrapper).props('visible')).toBe(false)
    expect((expandModalOf(wrapper).props('collection') as CollectionLike).id).toBe(2)
  })

  it('半智能合集可以装素材：显示「添加素材 / 批量选择」，纯智能合集不显示', async () => {
    const wrapper = mountView()
    await flushPromises()
    await switchCollection(wrapper, 4)

    expect(buttonWithText(wrapper, '添加素材')).toBeTruthy()
    expect(buttonWithText(wrapper, '批量选择')).toBeTruthy()

    await switchCollection(wrapper, 3)

    expect(buttonWithText(wrapper, '添加素材')).toBeFalsy()
    expect(buttonWithText(wrapper, '批量选择')).toBeFalsy()
  })

  it('手动合集同样显示添加入口（回归：不能因半智能改造把原有能力藏掉）', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(buttonWithText(wrapper, '添加素材')).toBeTruthy()
    expect(buttonWithText(wrapper, '批量选择')).toBeTruthy()
  })
})
