/**
 * 标签管理页核心逻辑（`useTagManage`）的补测（聚焦筛选排序/删除/扫描/拖拽/统计同步）。
 *
 * 既有 `useTagManage.test.ts` 只覆盖 URL 浏览状态持久化；本文件补：
 * - `filteredGroups` 的类别/来源/搜索/排序（置顶优先、usage/name/custom、空组移除）；
 * - 删除（单个/批量/未使用）、置顶、拖拽改类别与自定义排序的请求参数与失败提示；
 * - `onGridChanged` 的局部统计同步（素材移出后 usage_count/total_links 立即减）；
 * - `loadAll` 后按 id 刷新选中标签引用（标签已删除则回到空态）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchTagsGrouped: vi.fn(),
  fetchTagStats: vi.fn(),
  updateTag: vi.fn(),
  batchDeleteTags: vi.fn(),
  deleteUnusedTags: vi.fn(),
  findDuplicates: vi.fn(),
  reorderTags: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

vi.mock('@/api/tags', () => ({
  fetchTagsGrouped: mocks.fetchTagsGrouped,
  fetchTagStats: mocks.fetchTagStats,
  updateTag: mocks.updateTag,
  mergeTags: vi.fn(),
  batchDeleteTags: mocks.batchDeleteTags,
  deleteUnusedTags: mocks.deleteUnusedTags,
  findDuplicates: mocks.findDuplicates,
  reorderTags: mocks.reorderTags,
  createAlias: vi.fn(),
}))

vi.mock('@/constants/tag', () => ({
  CATEGORY_LABELS: { style: '风格', scene: '场景' },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useTagManage } from '../useTagManage'
import type { TagCategoryGroup, TagItem } from '@/api/tags'

function tag(id: number, name: string, over: Partial<TagItem> = {}): TagItem {
  return {
    id,
    name,
    category: 'style',
    source: 'manual',
    pinned: false,
    sort_order: id,
    description: null,
    usage_count: 3,
    ...over,
  }
}

function groupsFixture(): TagCategoryGroup[] {
  return [
    {
      category: 'style',
      tags: [
        tag(1, '法式', { usage_count: 5 }),
        tag(2, '韩系', { source: 'ai_generated', usage_count: 9 }),
      ],
    },
    {
      category: 'scene',
      tags: [tag(3, '通勤', { category: 'scene', usage_count: 20, pinned: true })],
    },
  ]
}

function statsFixture(over: Record<string, unknown> = {}) {
  return { total: 3, unused: 1, total_links: 30, by_source: {}, by_category: {}, ...over }
}

async function loaded(manage = useTagManage()) {
  await manage.loadAll()
  return manage
}

describe('useTagManage 数据与筛选', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchTagsGrouped.mockResolvedValue(groupsFixture())
    mocks.fetchTagStats.mockResolvedValue(statsFixture())
    mocks.updateTag.mockResolvedValue({})
    mocks.batchDeleteTags.mockResolvedValue({ deleted: 1 })
    mocks.deleteUnusedTags.mockResolvedValue({ message: '已删除 3 个未使用标签' })
    mocks.findDuplicates.mockResolvedValue({ duplicates: [], total: 0 })
    mocks.reorderTags.mockResolvedValue({ updated: 3 })
  })

  it('loadAll 回填分组与统计，loading 复位', async () => {
    const manage = await loaded()

    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
    expect(mocks.fetchTagStats).toHaveBeenCalledTimes(1)
    expect(manage.groups.value).toHaveLength(2)
    expect(manage.stats.value?.total_links).toBe(30)
    expect(manage.loading.value).toBe(false)
  })

  it('loadAll 失败：提示「加载失败」、loading 复位、分组保持空', async () => {
    mocks.fetchTagsGrouped.mockRejectedValue(new Error('500'))
    const manage = useTagManage()

    await manage.loadAll()

    expect(mocks.message.error).toHaveBeenCalledWith('加载失败')
    expect(manage.loading.value).toBe(false)
    expect(manage.groups.value).toEqual([])
  })

  it('filteredGroups：按类别 / 来源 / 搜索过滤，并移除空组', async () => {
    const manage = await loaded()

    manage.filterCategory.value = 'style'
    expect(manage.filteredGroups.value.map((g) => g.category)).toEqual(['style'])

    manage.filterCategory.value = null
    manage.filterSource.value = 'ai_generated'
    expect(manage.filteredGroups.value).toHaveLength(1)
    expect(manage.filteredGroups.value[0].tags.map((t) => t.id)).toEqual([2])

    manage.filterSource.value = null
    manage.searchQuery.value = '  法式 '
    expect(manage.filteredGroups.value).toHaveLength(1)
    expect(manage.filteredGroups.value[0].tags.map((t) => t.id)).toEqual([1])

    manage.searchQuery.value = '不存在的标签'
    expect(manage.filteredGroups.value).toEqual([])
  })

  it('排序 usage：置顶最前、同状态按使用次数降序', async () => {
    mocks.fetchTagsGrouped.mockResolvedValue([
      {
        category: 'style',
        tags: [
          tag(1, '低频', { usage_count: 1 }),
          tag(2, '置顶低', { pinned: true, usage_count: 2 }),
          tag(3, '高频', { usage_count: 9 }),
        ],
      },
    ])
    const manage = await loaded()

    expect(manage.filteredGroups.value[0].tags.map((t) => t.id)).toEqual([2, 3, 1])
  })

  it('排序 name：置顶最前、其余按中文排序', async () => {
    mocks.fetchTagsGrouped.mockResolvedValue([
      {
        category: 'style',
        tags: [tag(1, 'Beta'), tag(2, 'Zeta', { pinned: true }), tag(3, 'Alpha')],
      },
    ])
    const manage = await loaded()
    manage.sortMode.value = 'name'

    expect(manage.filteredGroups.value[0].tags.map((t) => t.name)).toEqual([
      'Zeta',
      'Alpha',
      'Beta',
    ])
  })

  it('排序 custom：保持后端顺序（不前端重排）', async () => {
    const manage = await loaded()
    manage.sortMode.value = 'custom'

    expect(manage.filteredGroups.value[0].tags.map((t) => t.id)).toEqual([1, 2])
  })

  it('hasActiveFilter：搜索/类别/来源任一非空即为 true', async () => {
    const manage = await loaded()
    expect(manage.hasActiveFilter.value).toBe(false)

    manage.searchQuery.value = '   '
    expect(manage.hasActiveFilter.value).toBe(false)

    manage.searchQuery.value = '法式'
    expect(manage.hasActiveFilter.value).toBe(true)

    manage.searchQuery.value = ''
    manage.filterCategory.value = 'style'
    expect(manage.hasActiveFilter.value).toBe(true)

    manage.filterCategory.value = null
    manage.filterSource.value = 'manual'
    expect(manage.hasActiveFilter.value).toBe(true)
  })

  it('选中：selectAllInGroup 加入整组、toggleSelect 单个切换、deselectAll 清空', async () => {
    const manage = await loaded()

    manage.selectAllInGroup(manage.groups.value[0])
    expect(Array.from(manage.selectedIds.value)).toEqual([1, 2])

    manage.toggleSelect(3)
    expect(manage.selectedIds.value.size).toBe(3)

    manage.deselectAll()
    expect(manage.selectedIds.value.size).toBe(0)
  })
})

describe('useTagManage 删除与扫描', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchTagsGrouped.mockResolvedValue(groupsFixture())
    mocks.fetchTagStats.mockResolvedValue(statsFixture())
    mocks.batchDeleteTags.mockResolvedValue({ deleted: 1 })
    mocks.deleteUnusedTags.mockResolvedValue({ message: '已删除 3 个未使用标签' })
    mocks.findDuplicates.mockResolvedValue({ duplicates: [], total: 0 })
  })

  it('handleDelete：删单个后清选中并刷新', async () => {
    const manage = await loaded()
    manage.toggleSelect(2)

    await manage.handleDelete(2, '韩系')

    expect(mocks.batchDeleteTags).toHaveBeenCalledWith([2])
    expect(mocks.message.success).toHaveBeenCalledWith('已删除 "韩系"')
    expect(manage.selectedIds.value.size).toBe(0)
    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(2)
  })

  it('handleDelete 失败：提示「删除失败」且不刷新', async () => {
    mocks.batchDeleteTags.mockRejectedValue(new Error('500'))
    const manage = await loaded()

    await manage.handleDelete(1, '法式')

    expect(mocks.message.error).toHaveBeenCalledWith('删除失败')
    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
  })

  it('handleBatchDelete：未选中不发请求；选中后批量删并清空选择', async () => {
    const manage = await loaded()
    await manage.handleBatchDelete()
    expect(mocks.batchDeleteTags).not.toHaveBeenCalled()

    manage.toggleSelect(1)
    manage.toggleSelect(3)
    await manage.handleBatchDelete()

    expect(mocks.batchDeleteTags).toHaveBeenCalledWith([1, 3])
    expect(mocks.message.success).toHaveBeenCalledWith('已删除 2 个标签')
    expect(manage.selectedIds.value.size).toBe(0)
  })

  it('handleBatchDelete 失败：提示「批量删除失败」', async () => {
    mocks.batchDeleteTags.mockRejectedValue(new Error('500'))
    const manage = await loaded()
    manage.toggleSelect(1)

    await manage.handleBatchDelete()

    expect(mocks.message.error).toHaveBeenCalledWith('批量删除失败')
  })

  it('handleDeleteUnused：成功用后端 message 提示并刷新；失败带 detail 前缀', async () => {
    const manage = await loaded()

    await manage.handleDeleteUnused()
    expect(mocks.message.success).toHaveBeenCalledWith('已删除 3 个未使用标签')
    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(2)

    mocks.deleteUnusedTags.mockRejectedValueOnce({ response: { data: { detail: '后端拒绝' } } })
    await manage.handleDeleteUnused()
    expect(mocks.message.error).toHaveBeenCalledWith('删除失败：后端拒绝')

    mocks.deleteUnusedTags.mockRejectedValueOnce({})
    await manage.handleDeleteUnused()
    expect(mocks.message.error).toHaveBeenCalledWith('删除失败：未知错误')
  })

  it('scanDuplicates：无重复给 success、有重复给 info，失败提示扫描失败', async () => {
    const manage = await loaded()

    await manage.scanDuplicates()
    expect(mocks.findDuplicates).toHaveBeenCalledWith(0.75)
    expect(manage.showDuplicatesPanel.value).toBe(true)
    expect(manage.duplicatePairs.value).toEqual([])
    expect(mocks.message.success).toHaveBeenCalledWith('未发现重复标签')
    expect(manage.scanningDuplicates.value).toBe(false)

    mocks.findDuplicates.mockResolvedValueOnce({
      duplicates: [{ tag_a: tag(1, '法式'), tag_b: tag(2, '韩系'), similarity: 0.9 }],
      total: 1,
    })
    await manage.scanDuplicates()
    expect(mocks.message.info).toHaveBeenCalledWith('发现 1 对相似标签')

    mocks.findDuplicates.mockRejectedValueOnce(new Error('500'))
    await manage.scanDuplicates()
    expect(mocks.message.error).toHaveBeenCalledWith('扫描失败')
    expect(manage.scanningDuplicates.value).toBe(false)
  })

  it('scanDuplicates 使用当前阈值', async () => {
    const manage = await loaded()
    manage.duplicateThreshold.value = 0.9

    await manage.scanDuplicates()

    expect(mocks.findDuplicates).toHaveBeenCalledWith(0.9)
  })
})

describe('useTagManage 修改与排序', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchTagsGrouped.mockResolvedValue(groupsFixture())
    mocks.fetchTagStats.mockResolvedValue(statsFixture())
    mocks.updateTag.mockResolvedValue({})
    mocks.reorderTags.mockResolvedValue({ updated: 2 })
  })

  it('togglePin：翻转 pinned 并提示置顶/取消置顶', async () => {
    const manage = await loaded()
    const plain = manage.groups.value[0].tags[0]
    const pinned = manage.groups.value[1].tags[0]

    await manage.togglePin(plain)
    expect(mocks.updateTag).toHaveBeenCalledWith(1, { pinned: true })
    expect(mocks.message.success).toHaveBeenCalledWith('已置顶')

    await manage.togglePin(pinned)
    expect(mocks.updateTag).toHaveBeenLastCalledWith(3, { pinned: false })
    expect(mocks.message.success).toHaveBeenLastCalledWith('已取消置顶')
  })

  it('togglePin 失败：提示「操作失败」', async () => {
    mocks.updateTag.mockRejectedValue(new Error('500'))
    const manage = await loaded()

    await manage.togglePin(manage.groups.value[0].tags[0])

    expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
  })

  it('onDrop：同类别不请求；换类别按中文类别名提示', async () => {
    const manage = await loaded()
    const styleTag = manage.groups.value[0].tags[0]

    await manage.onDrop(styleTag, 'style')
    expect(mocks.updateTag).not.toHaveBeenCalled()

    await manage.onDrop(styleTag, 'scene')
    expect(mocks.updateTag).toHaveBeenCalledWith(1, { category: 'scene' })
    expect(mocks.message.success).toHaveBeenCalledWith('已将 "法式" 移至 场景')
  })

  it('onDrop 失败：提示「移动失败」', async () => {
    mocks.updateTag.mockRejectedValue(new Error('400'))
    const manage = await loaded()

    await manage.onDrop(manage.groups.value[0].tags[0], 'scene')

    expect(mocks.message.error).toHaveBeenCalledWith('移动失败')
  })

  it('onGridChanged：removed=0 整体刷新；removed>0 局部减 usage/total_links', async () => {
    const manage = await loaded()
    manage.selectTag(manage.groups.value[0].tags[0]) // usage 5
    const fetchCalls = mocks.fetchTagsGrouped.mock.calls.length

    manage.onGridChanged({ removed: 0 })
    await Promise.resolve()
    expect(mocks.fetchTagsGrouped.mock.calls.length).toBe(fetchCalls + 1)

    manage.onGridChanged({ removed: 2 })
    expect(manage.selectedTag.value?.usage_count).toBe(3)
    expect(manage.stats.value?.total_links).toBe(28)

    manage.onGridChanged({ removed: 99 })
    expect(manage.selectedTag.value?.usage_count).toBe(0)
    expect(manage.stats.value?.total_links).toBe(0)
  })

  it('onTagDrop：向后拖动按 off-by-one 计算插入位并提交 sort_order', async () => {
    mocks.fetchTagsGrouped.mockResolvedValue([
      { category: 'style', tags: [tag(1, 'A'), tag(2, 'B'), tag(3, 'C')] },
    ])
    const manage = await loaded()
    manage.sortMode.value = 'custom'
    const [a, , c] = manage.groups.value[0].tags

    await manage.onTagDrop(c, a)

    expect(mocks.reorderTags).toHaveBeenCalledWith([
      { id: 2, sort_order: 0 },
      { id: 1, sort_order: 1 },
      { id: 3, sort_order: 2 },
    ])
    expect(mocks.message.success).toHaveBeenCalledWith('排序已更新')
  })

  it('onTagDrop：非自定义排序 / 筛选态 / 同 id / 跨分组都不提交', async () => {
    const manage = await loaded()
    const [a, b] = manage.groups.value[0].tags

    await manage.onTagDrop(b, a) // sortMode=usage
    expect(mocks.reorderTags).not.toHaveBeenCalled()

    manage.sortMode.value = 'custom'
    manage.searchQuery.value = '法'
    await manage.onTagDrop(b, a)
    expect(mocks.message.warning).toHaveBeenCalledWith('筛选状态下不支持拖动排序，请清除筛选后重试')
    expect(mocks.reorderTags).not.toHaveBeenCalled()

    manage.searchQuery.value = ''
    await manage.onTagDrop(a, a)
    expect(mocks.reorderTags).not.toHaveBeenCalled()

    // 跨分组（style 的 a 拖到 scene 的 c）
    await manage.onTagDrop(manage.groups.value[1].tags[0], a)
    expect(mocks.reorderTags).not.toHaveBeenCalled()
  })

  it('onTagMove：边界与置顶不移动；正常相邻交换后提交', async () => {
    mocks.fetchTagsGrouped.mockResolvedValue([
      { category: 'style', tags: [tag(1, 'A'), tag(2, 'B'), tag(3, 'C', { pinned: true })] },
    ])
    const manage = await loaded()
    manage.sortMode.value = 'custom'
    const [a, , c] = manage.groups.value[0].tags

    await manage.onTagMove(a, 'up')
    await manage.onTagMove(a, 'down') // A 与 B 交换
    expect(mocks.reorderTags).toHaveBeenLastCalledWith([
      { id: 2, sort_order: 0 },
      { id: 1, sort_order: 1 },
      { id: 3, sort_order: 2 },
    ])

    mocks.reorderTags.mockClear()
    await manage.onTagMove(c, 'down')
    expect(mocks.reorderTags).not.toHaveBeenCalled() // 已到末尾
  })

  it('loadAll 后按 id 刷新选中标签；标签已删除则回到空态', async () => {
    const manage = await loaded()
    manage.selectTag(manage.groups.value[0].tags[0])
    expect(manage.selectedTag.value?.name).toBe('法式')

    mocks.fetchTagsGrouped.mockResolvedValueOnce([
      { category: 'style', tags: [tag(1, '法式（改名）')] },
    ])
    await manage.loadAll()
    expect(manage.selectedTag.value?.name).toBe('法式（改名）')

    mocks.fetchTagsGrouped.mockResolvedValueOnce([{ category: 'style', tags: [tag(2, '韩系')] }])
    await manage.loadAll()
    expect(manage.selectedTag.value).toBeNull()
  })
})
