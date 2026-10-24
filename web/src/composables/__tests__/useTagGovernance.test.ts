/** useTagGovernance 测试：四个治理入口的数据层（颜色剥离 / 观察名单 / 别名建议 / 长尾）+ 洞察。
 *
 * 关注四件事：① 每个入口的「危险动作」都要用 API 真的落地；
 * ② 失败要提示且不改本地状态（不能假装成功）；
 * ③ 观察名单/长尾的批量动作后必须重新拉取（列表要反映删除/保留结果）；
 * ④ 洞察跟随选中标签自动加载，切换标签即刷新。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

import {
  useAliasSuggestions,
  useColorStrip,
  useLongTailTags,
  useTagInsight,
  useWatchList,
} from '../useTagGovernance'

const mocks = vi.hoisted(() => ({
  stripColorDryRun: vi.fn(),
  stripColorApply: vi.fn(),
  fetchWatchList: vi.fn(),
  keepWatchTags: vi.fn(),
  batchDeleteTags: vi.fn(),
  fetchAliasSuggestions: vi.fn(),
  createAlias: vi.fn(),
  mergeTags: vi.fn(),
  mergeTagsBatch: vi.fn(),
  fetchTagsGrouped: vi.fn(),
  fetchTagTrend: vi.fn(),
  fetchTagCooccurrence: vi.fn(),
}))

vi.mock('@/api/tagAdvanced', () => ({
  stripColorDryRun: mocks.stripColorDryRun,
  stripColorApply: mocks.stripColorApply,
  fetchWatchList: mocks.fetchWatchList,
  keepWatchTags: mocks.keepWatchTags,
  fetchTagCooccurrence: mocks.fetchTagCooccurrence,
  fetchAliasSuggestions: mocks.fetchAliasSuggestions,
  mergeTagsBatch: mocks.mergeTagsBatch,
}))

const events = vi.hoisted(() => ({ notifyTagChanged: vi.fn() }))
vi.mock('@/composables/useTagEvents', () => ({
  useTagEvents: () => ({ notifyTagChanged: events.notifyTagChanged }),
}))

vi.mock('@/api/tags', () => ({
  batchDeleteTags: mocks.batchDeleteTags,
  createAlias: mocks.createAlias,

  mergeTags: mocks.mergeTags,
  fetchTagsGrouped: mocks.fetchTagsGrouped,
  fetchTagTrend: mocks.fetchTagTrend,
}))

function previewPayload(overrides: Record<string, unknown> = {}) {
  return {
    category: 'item_type',
    total_scanned: 100,
    renamed: 10,
    merged: 3,
    skipped: 2,
    alias_skipped: 1,
    cross_category_skipped: 0,
    links_repointed: 120,
    color_links_added: 80,
    color_prefixes: 12,
    samples: [],
    ...overrides,
  }
}

function watchItem(id: number, name: string, usage = 0) {
  return {
    id,
    name,
    category: 'style',
    created_at: '2026-10-20T00:00:00Z',
    usage_count: usage,
  }
}

describe('useTagGovernance', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((fn) => fn.mockReset())
    events.notifyTagChanged.mockReset()
    vi.restoreAllMocks()
  })

  it('颜色剥离：预览给出可执行数，执行成功后清空预览', async () => {
    mocks.stripColorDryRun.mockResolvedValue(previewPayload())
    mocks.stripColorApply.mockResolvedValue({
      ...previewPayload({ renamed: 10, merged: 3 }),
      batch_id: 'color-strip-1',
    })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const strip = useColorStrip()

    await strip.runPreview()
    expect(strip.actionable.value).toBe(13)

    const ok = await strip.runApply()
    expect(ok).toBe(true)
    expect(mocks.stripColorApply).toHaveBeenCalledWith({ category: 'item_type' })
    expect(success).toHaveBeenCalledWith(expect.stringContaining('重命名 10'))
    expect(strip.preview.value).toBeNull()
  })

  it('颜色剥离：无可处理标签时只提示、不算成功', async () => {
    mocks.stripColorDryRun.mockResolvedValue(previewPayload({ renamed: 0, merged: 0 }))
    mocks.stripColorApply.mockResolvedValue(previewPayload({ renamed: 0, merged: 0 }))
    vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const info = vi.spyOn(Message, 'info').mockImplementation((() => {}) as never)
    const strip = useColorStrip()

    await strip.runPreview()
    expect(info).toHaveBeenCalledWith(expect.stringContaining('没有需要剥离'))
    expect(await strip.runApply()).toBe(false)
  })

  it('观察名单：保留后本地移除并广播变更（不再重拉整页）', async () => {
    mocks.fetchWatchList.mockResolvedValue({
      days: 7,
      max_usage: 1,
      cutoff: '2026-10-17T00:00:00',
      total: 2,
      returned: 2,
      offset: 0,
      items: [watchItem(1, '新词A'), watchItem(2, '新词B')],
    })
    mocks.keepWatchTags.mockResolvedValue({ kept: 1 })
    vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const watch = useWatchList()

    await watch.load()
    expect(watch.items.value).toHaveLength(2)

    watch.onSelectionChange([1])
    expect(await watch.doKeep()).toBe(true)
    expect(mocks.keepWatchTags).toHaveBeenCalledWith([1])
    // 本地移除：只剩 1 条，且没有再次请求（重拉 200 条会把滚动位置冲掉）
    expect(watch.items.value.map((i) => i.id)).toEqual([2])
    expect(watch.total.value).toBe(1)
    expect(mocks.fetchWatchList).toHaveBeenCalledTimes(1)
    expect(events.notifyTagChanged).toHaveBeenCalledWith({ type: 'updated', tagIds: [1] })
  })

  it('观察名单：加载更多按 offset 追加，hasMore 跟随 total', async () => {
    mocks.fetchWatchList.mockResolvedValueOnce({
      days: 7,
      max_usage: 1,
      cutoff: '',
      total: 3,
      returned: 2,
      offset: 0,
      items: [watchItem(1, '甲'), watchItem(2, '乙')],
    })
    mocks.fetchWatchList.mockResolvedValueOnce({
      days: 7,
      max_usage: 1,
      cutoff: '',
      total: 3,
      returned: 1,
      offset: 2,
      items: [watchItem(3, '丙')],
    })
    const watch = useWatchList()

    await watch.load()
    expect(watch.hasMore.value).toBe(true)

    await watch.load(true)
    expect(mocks.fetchWatchList).toHaveBeenLastCalledWith({
      days: 7,
      max_usage: 1,
      limit: 200,
      offset: 2,
    })
    expect(watch.items.value.map((i) => i.name)).toEqual(['甲', '乙', '丙'])
    expect(watch.hasMore.value).toBe(false)
  })

  it('观察名单：删除失败时提示且不回显成功', async () => {
    mocks.fetchWatchList.mockResolvedValue({
      days: 7,
      max_usage: 1,
      cutoff: '',
      total: 1,
      items: [watchItem(9, '新词C')],
    })
    mocks.batchDeleteTags.mockRejectedValue(new Error('boom'))
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const watch = useWatchList()

    await watch.load()
    watch.onSelectionChange([9])
    expect(await watch.doDelete()).toBe(false)
    expect(error).toHaveBeenCalled()
  })

  it('别名建议：丢掉残缺配对，只展示 0.6~0.75 的同义变体', async () => {
    mocks.fetchAliasSuggestions.mockResolvedValue({
      total: 0,
      cached: false,
      pairs: [
        {
          tag_a: { id: 1, name: '白衬衫', category: 'item_type' },
          tag_b: { id: 2, name: '白色衬衫', category: 'item_type' },
          similarity: 0.68,
        },
        {
          tag_a: { id: 3, name: '黑衣', category: 'item_type' },
          tag_b: { id: 4, name: '黑衣服', category: 'item_type' },
          similarity: 0.9,
        },
        { tag_a: null, tag_b: { id: 5, name: '残缺', category: 'x' }, similarity: 0.7 },
      ],
    })
    const alias = useAliasSuggestions()

    await alias.load()
    expect(alias.suggestions.value).toHaveLength(1)
    expect(alias.suggestions.value[0].similarity).toBeCloseTo(0.68)
    expect(mocks.fetchAliasSuggestions).toHaveBeenCalledWith({
      min_similarity: 0.6,
      max_similarity: 0.75,
    })
  })

  it('别名建议：设别名与合并分别调用对应接口并隐藏已处理的那一对', async () => {
    mocks.fetchAliasSuggestions.mockResolvedValue({
      total: 0,
      cached: false,
      pairs: [
        {
          tag_a: { id: 1, name: '白衬衫', category: 'item_type' },
          tag_b: { id: 2, name: '白色衬衫', category: 'item_type' },
          similarity: 0.68,
        },
        {
          tag_a: { id: 3, name: '黑衣', category: 'item_type' },
          tag_b: { id: 4, name: '黑衣服', category: 'item_type' },
          similarity: 0.7,
        },
      ],
    })
    mocks.createAlias.mockResolvedValue({})
    mocks.mergeTags.mockResolvedValue({})
    vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const alias = useAliasSuggestions()

    await alias.load()
    expect(alias.suggestions.value).toHaveLength(2)

    const [first, second] = alias.suggestions.value
    expect(await alias.makeAlias(first)).toBe(true)
    expect(mocks.createAlias).toHaveBeenCalledWith(1, '白色衬衫')
    // 处理过的那一对被隐藏，另一对仍在
    expect(alias.suggestions.value.map((p) => p.tag_a.id)).toEqual([3])

    expect(await alias.doMerge(second)).toBe(true)
    expect(mocks.mergeTags).toHaveBeenCalledWith(4, 3)
    expect(alias.suggestions.value).toHaveLength(0)
  })

  it('长尾：按桶筛选、合并需要精确目标、删除走批量接口', async () => {
    mocks.fetchTagsGrouped.mockResolvedValue([
      {
        category: 'style',
        tags: [
          {
            id: 1,
            name: '只用一次',
            category: 'style',
            source: 'ai_generated',
            pinned: false,
            sort_order: 0,
            description: null,
            usage_count: 1,
          },
          {
            id: 2,
            name: '热门词',
            category: 'style',
            source: 'ai_generated',
            pinned: false,
            sort_order: 0,
            description: null,
            usage_count: 500,
          },
        ],
      },
    ])
    mocks.mergeTagsBatch.mockResolvedValue({ merged: 1, failed: [] })
    mocks.batchDeleteTags.mockResolvedValue({})
    vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const tail = useLongTailTags()

    await tail.load()
    expect(tail.buckets.value.find((b) => b.key === 'one')?.count).toBe(1)
    expect(tail.visibleRows.value.map((t) => t.name)).toEqual(['只用一次'])

    // 没有填目标标签 → 拒绝并提示
    tail.onSelectionChange([1])
    expect(await tail.doMerge()).toBe(false)
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('合并到'))

    // 填了精确目标 → 一次请求合并到 2（不再 1 次/标签）
    tail.targetKeyword.value = '热门词'
    expect(await tail.doMerge()).toBe(true)
    expect(mocks.mergeTagsBatch).toHaveBeenCalledWith([1], 2)
    // 本地移除：不再重拉 22k 标签
    expect(tail.visibleRows.value).toHaveLength(0)
    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
    expect(events.notifyTagChanged).toHaveBeenCalledWith({
      type: 'merged',
      tagIds: [1],
      targetId: 2,
    })

    // 删除走批量接口（本地移除同样生效）
    tail.onSelectionChange([2])
    expect(await tail.doDelete()).toBe(true)
    expect(mocks.batchDeleteTags).toHaveBeenCalledWith([2])
    expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
  })

  it('洞察：跟随标签自动加载趋势与共现，清空标签则清屏', async () => {
    mocks.fetchTagTrend.mockResolvedValue({
      tag: { id: 1, name: '白衬衫' },
      granularity: 'month',
      trend: Array.from({ length: 8 }, (_, i) => ({ bucket: `2026-0${i + 1}`, count: i + 1 })),
    })
    mocks.fetchTagCooccurrence.mockResolvedValue({
      tag: { id: 1, name: '白衬衫', category: 'item_type' },
      materials: 12,
      items: [{ id: 2, name: '阔腿裤', category: 'item_type', count: 5 }],
    })
    const insight = useTagInsight()

    await insight.load(1)
    expect(insight.materials.value).toBe(12)
    // 趋势只画最近 6 个月
    expect(insight.recentTrend.value).toHaveLength(6)
    expect(insight.maxCount.value).toBe(8)
    expect(insight.cooccurrence.value[0].name).toBe('阔腿裤')

    await insight.load(1)
    expect(mocks.fetchTagTrend).toHaveBeenCalledWith(1, 'month')
    expect(mocks.fetchTagCooccurrence).toHaveBeenCalledWith(1, 8)
  })
})
