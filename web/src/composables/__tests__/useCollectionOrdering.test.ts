/**
 * 合集拖拽排序与「编辑成员顺序」的测试。
 *
 * 这两段逻辑都在 composables 里（视图只做接线），但它们的失败模式很"安静"：
 * 拖完顺序没变、乐观更新后没回滚、保存失败还以为成功了——用户只会觉得「拖了没用」。
 *
 * 所以断言三件事：**顺序真的变了**、**该发的请求发了**、**失败要回滚并提示**。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref, computed } from 'vue'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchCollectionInspirations: vi.fn(),
  reorderCollectionItems: vi.fn(),
  reorderCollections: vi.fn(),
}))

vi.mock('@/api/collections', () => ({
  fetchCollectionInspirations: mocks.fetchCollectionInspirations,
  reorderCollectionItems: mocks.reorderCollectionItems,
  reorderCollections: mocks.reorderCollections,
}))

import { useCollectionItemOrdering } from '../useCollectionItemOrdering'
import { useCollectionListDnd } from '../useCollectionListDnd'
import type { CollectionOut } from '@/api/collections'
import type { InspirationOut } from '@/api/inspirations'

function collection(id: number, name = `合集${id}`): CollectionOut {
  return {
    id,
    name,
    description: null,
    kind: 'manual',
    position: id,
    parent_id: null,
    child_count: 0,
    item_count: 0,
    query_json: null,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    auto_source: null,
    created_at: '2026-10-21T00:00:00',
    updated_at: '2026-10-21T00:00:00',
  } as CollectionOut
}

function item(id: string): InspirationOut {
  return { id, file_path: `images/${id}.jpg`, thumbnail_path: null } as InspirationOut
}

function mountOrdering(total = 10) {
  const current = computed(() => collection(1))
  const totalRef = ref(total)
  const loadContent = vi.fn()
  const api = useCollectionItemOrdering({ current, total: totalRef, loadContent })
  return { api, loadContent, totalRef }
}

describe('useCollectionItemOrdering（合集内成员排序）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchCollectionInspirations.mockResolvedValue({
      items: [item('a'), item('b'), item('c')],
      total: 3,
    })
    mocks.reorderCollectionItems.mockResolvedValue({ updated: 3 })
  })

  it('成员过多时拒绝进入排序模式并提示先筛选', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const { api } = mountOrdering(500)

    api.enterOrdering()

    expect(api.ordering.value).toBe(false)
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('素材过多'))
    expect(mocks.fetchCollectionInspirations).not.toHaveBeenCalled()
    warn.mockRestore()
  })

  it('进入排序模式后一次拉全量成员', async () => {
    const { api } = mountOrdering(3)

    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    expect(api.ordering.value).toBe(true)
    expect(mocks.fetchCollectionInspirations).toHaveBeenCalledWith(1, { page: 1, size: 500 })
  })

  it('加载成员失败：提示错误且不进排序（空列表）', async () => {
    const err = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.fetchCollectionInspirations.mockRejectedValue(new Error('网络错误'))
    const { api } = mountOrdering(3)

    api.enterOrdering()
    await vi.waitFor(() => expect(err).toHaveBeenCalled())

    expect(api.orderedItems.value).toEqual([])
    err.mockRestore()
  })

  it('把第一个拖到第三个之后：顺序正确重排', async () => {
    const { api } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    api.onItemDragStart('a')
    await api.onItemDrop('c')

    expect(api.orderedItems.value.map((i) => i.id)).toEqual(['b', 'c', 'a'])
  })

  it('把最后一个拖到最前：顺序正确重排', async () => {
    const { api } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    api.onItemDragStart('c')
    await api.onItemDrop('a')

    expect(api.orderedItems.value.map((i) => i.id)).toEqual(['c', 'a', 'b'])
  })

  it('拖到自己身上 / 没设置拖拽源：顺序不变', async () => {
    const { api } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    api.onItemDragStart('a')
    await api.onItemDrop('a')
    expect(api.orderedItems.value.map((i) => i.id)).toEqual(['a', 'b', 'c'])

    await api.onItemDrop('b') // draggingItemId 已在上次 drop 后清空
    expect(api.orderedItems.value.map((i) => i.id)).toEqual(['a', 'b', 'c'])
  })

  it('目标不在列表里：忽略，不发请求', async () => {
    const { api } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    api.onItemDragStart('a')
    await api.onItemDrop('不存在')

    expect(api.orderedItems.value.map((i) => i.id)).toEqual(['a', 'b', 'c'])
    expect(mocks.reorderCollectionItems).not.toHaveBeenCalled()
  })

  it('保存成功：提交当前顺序 + 退出排序模式 + 刷新内容', async () => {
    const ok = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const { api, loadContent } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))
    api.onItemDragStart('c')
    await api.onItemDrop('a')

    await api.saveItemOrder()

    expect(mocks.reorderCollectionItems).toHaveBeenCalledWith(1, ['c', 'a', 'b'])
    expect(api.ordering.value).toBe(false)
    expect(loadContent).toHaveBeenCalled()
    ok.mockRestore()
  })

  it('保存失败：提示错误并**留在排序模式**（否则用户的编排白做了）', async () => {
    const err = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.reorderCollectionItems.mockRejectedValue(new Error('保存失败'))
    const { api, loadContent } = mountOrdering(3)
    api.enterOrdering()
    await vi.waitFor(() => expect(api.orderedItems.value).toHaveLength(3))

    await api.saveItemOrder()

    expect(api.ordering.value).toBe(true)
    expect(loadContent).not.toHaveBeenCalled()
    expect(err).toHaveBeenCalled()
    err.mockRestore()
  })
})

describe('useCollectionListDnd（合集列表拖拽）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.reorderCollections.mockResolvedValue({ updated: 3 })
  })

  it('拖拽后乐观重排 + 提交顺序 + 重新拉取对齐', async () => {
    const collections = ref([collection(1), collection(2), collection(3)])
    const loadCollections = vi.fn().mockResolvedValue(undefined)
    const api = useCollectionListDnd({ collections, loadCollections })

    api.onCollectionDragStart(1)
    await api.onCollectionDrop(3)

    expect(collections.value.map((c) => c.id)).toEqual([2, 3, 1])
    expect(mocks.reorderCollections).toHaveBeenCalledWith([2, 3, 1])
    expect(loadCollections).toHaveBeenCalled()
  })

  it('拖到自己身上：不排序也不发请求', async () => {
    const collections = ref([collection(1), collection(2)])
    const loadCollections = vi.fn().mockResolvedValue(undefined)
    const api = useCollectionListDnd({ collections, loadCollections })

    api.onCollectionDragStart(1)
    await api.onCollectionDrop(1)

    expect(collections.value.map((c) => c.id)).toEqual([1, 2])
    expect(mocks.reorderCollections).not.toHaveBeenCalled()
  })

  it('保存失败：提示错误并回滚（重新拉取服务端顺序）', async () => {
    const err = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.reorderCollections.mockRejectedValue(new Error('boom'))
    const collections = ref([collection(1), collection(2), collection(3)])
    const loadCollections = vi.fn().mockResolvedValue(undefined)
    const api = useCollectionListDnd({ collections, loadCollections })

    api.onCollectionDragStart(1)
    await api.onCollectionDrop(2)

    expect(loadCollections).toHaveBeenCalled()
    expect(err).toHaveBeenCalled()
    err.mockRestore()
  })

  it('拖动结束后清掉 dragOver 高亮', async () => {
    const collections = ref([collection(1), collection(2)])
    const api = useCollectionListDnd({
      collections,
      loadCollections: vi.fn().mockResolvedValue(undefined),
    })

    api.dragOverId.value = 2
    api.onCollectionDragStart(1)
    await api.onCollectionDrop(2)

    expect(api.dragOverId.value).toBeNull()
  })
})
