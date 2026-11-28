/**
 * 标签治理（useTagGovernance）**补齐用例**：既有 useTagGovernance.test.ts 覆盖四个入口的主
 * 成功路径与部分失败，本文件补未覆盖的失败/边界分支：
 *
 * - 颜色剥离：预览/执行失败的提示与 loading 复位；
 * - 观察名单：加载失败、保留/删除的「空选择」保护、保留失败、删除成功后的本地移除与广播；
 * - 别名建议：扫描失败、设别名失败、合并失败；
 * - 标签洞察：加载失败清空数据、follow 跟随选中标签（有 id 加载 / 清空 id 清屏）；
 * - 长尾标签：未知分桶、加载失败、切桶清空选择、无目标/无可合并项警告、批量合并失败、
 *   删除空选择与删除失败。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

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
  notifyTagChanged: vi.fn(),
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

vi.mock('@/api/tags', () => ({
  batchDeleteTags: mocks.batchDeleteTags,
  createAlias: mocks.createAlias,
  mergeTags: mocks.mergeTags,
  fetchTagsGrouped: mocks.fetchTagsGrouped,
  fetchTagTrend: mocks.fetchTagTrend,
}))

vi.mock('@/composables/useTagEvents', () => ({
  useTagEvents: () => ({ notifyTagChanged: mocks.notifyTagChanged }),
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return {
    ...actual,
    Message: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    },
  }
})

import { Message } from '@arco-design/web-vue'
import {
  useAliasSuggestions,
  useColorStrip,
  useLongTailTags,
  useTagInsight,
  useWatchList,
} from '../useTagGovernance'

function tag(id: number, name: string, usage = 1) {
  return {
    id,
    name,
    category: 'style',
    usage_count: usage,
    created_at: '2026-10-20T00:00:00Z',
  }
}

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

beforeEach(() => {
  vi.clearAllMocks()
  mocks.notifyTagChanged.mockReset()
})

describe('颜色剥离失败分支', () => {
  it('预览失败：提示「预览失败」，previewing 复位、不清空旧预览', async () => {
    mocks.stripColorDryRun.mockRejectedValue({ response: { data: { detail: '扫描超时' } } })
    const api = useColorStrip()
    const previous = previewPayload()
    api.preview.value = previous as never

    await api.runPreview()

    expect(Message.error).toHaveBeenCalledWith('扫描超时')
    expect(api.previewing.value).toBe(false)
    expect(api.preview.value).toEqual(previous)
  })

  it('执行失败：提示「执行失败」，返回 false，applying 复位', async () => {
    mocks.stripColorApply.mockRejectedValue({ response: { data: { detail: '写历史失败' } } })
    const api = useColorStrip()

    await expect(api.runApply()).resolves.toBe(false)

    expect(Message.error).toHaveBeenCalledWith('写历史失败')
    expect(api.applying.value).toBe(false)
    expect(mocks.notifyTagChanged).not.toHaveBeenCalled()
  })
})

describe('观察名单失败与空选择', () => {
  it('加载失败：提示「加载观察名单失败」，loading 复位', async () => {
    mocks.fetchWatchList.mockRejectedValue({ response: { data: {} } })
    const api = useWatchList()

    await api.load()

    expect(Message.error).toHaveBeenCalledWith('加载观察名单失败')
    expect(api.loading.value).toBe(false)
  })

  it('未勾选时保留/删除都直接返回 false，不发请求', async () => {
    const api = useWatchList()

    await expect(api.doKeep()).resolves.toBe(false)
    await expect(api.doDelete()).resolves.toBe(false)

    expect(mocks.keepWatchTags).not.toHaveBeenCalled()
    expect(mocks.batchDeleteTags).not.toHaveBeenCalled()
    expect(api.busy.value).toBe(false)
  })

  it('保留失败：提示「确认保留失败」，返回 false，busy 复位', async () => {
    mocks.keepWatchTags.mockRejectedValue({ response: { data: {} } })
    const api = useWatchList()
    api.selected.value = [1]

    await expect(api.doKeep()).resolves.toBe(false)

    expect(Message.error).toHaveBeenCalledWith('确认保留失败')
    expect(api.busy.value).toBe(false)
  })

  it('删除成功：批量删除、本地移除、广播 deleted', async () => {
    mocks.batchDeleteTags.mockResolvedValue({})
    const api = useWatchList()
    api.items.value = [tag(1, '新标签A'), tag(2, '新标签B')] as never
    api.total.value = 2
    api.selected.value = [1]

    await expect(api.doDelete()).resolves.toBe(true)

    expect(mocks.batchDeleteTags).toHaveBeenCalledWith([1])
    expect(Message.success).toHaveBeenCalledWith('已删除 1 个标签')
    expect(api.items.value.map((item) => item.id)).toEqual([2])
    expect(api.total.value).toBe(1)
    expect(api.selected.value).toEqual([])
    expect(mocks.notifyTagChanged).toHaveBeenCalledWith({ type: 'deleted', tagIds: [1] })
    expect(api.busy.value).toBe(false)
  })
})

describe('别名建议失败分支', () => {
  it('扫描失败：提示「扫描失败」，loading 复位', async () => {
    mocks.fetchAliasSuggestions.mockRejectedValue({
      response: { data: { detail: '相似度扫描失败' } },
    })
    const api = useAliasSuggestions()

    await api.load()

    expect(Message.error).toHaveBeenCalledWith('相似度扫描失败')
    expect(api.loading.value).toBe(false)
  })

  it('设别名失败：提示「设别名失败」，返回 false，不隐藏该对', async () => {
    mocks.createAlias.mockRejectedValue({ response: { data: {} } })
    const api = useAliasSuggestions()
    const pair = {
      similarity: 0.7,
      tag_a: tag(1, '法式'),
      tag_b: tag(2, '法风'),
    }
    mocks.fetchAliasSuggestions.mockResolvedValueOnce({ pairs: [pair] })
    await api.load()
    expect(api.suggestions.value).toHaveLength(1)

    await expect(api.makeAlias(pair as never)).resolves.toBe(false)

    expect(Message.error).toHaveBeenCalledWith('设别名失败')
    expect(api.suggestions.value).toHaveLength(1)
    expect(api.busy.value).toBe(false)
  })

  it('合并失败：提示「合并失败」，返回 false', async () => {
    mocks.mergeTags.mockRejectedValue({ response: { data: {} } })
    const api = useAliasSuggestions()
    const pair = {
      similarity: 0.7,
      tag_a: tag(1, '法式'),
      tag_b: tag(2, '法风'),
    }

    await expect(api.doMerge(pair as never)).resolves.toBe(false)

    expect(Message.error).toHaveBeenCalledWith('合并失败')
    expect(api.busy.value).toBe(false)
  })
})

describe('标签洞察失败与 follow', () => {
  it('加载失败：提示并清空趋势/共现，loading 复位', async () => {
    mocks.fetchTagTrend.mockRejectedValue({ response: { data: { detail: 'trend 500' } } })
    mocks.fetchTagCooccurrence.mockResolvedValue({ items: [] })
    const api = useTagInsight()
    api.trend.value = [{ bucket: '2026-10', count: 3 }]
    api.cooccurrence.value = [{ id: 1, name: '白裙', category: 'style', count: 1 }]

    await api.load(3)

    expect(Message.error).toHaveBeenCalledWith('trend 500')
    expect(api.trend.value).toEqual([])
    expect(api.cooccurrence.value).toEqual([])
    expect(api.loading.value).toBe(false)
  })

  it('follow：选中标签即加载；清空选中即清屏', async () => {
    mocks.fetchTagTrend.mockResolvedValue({ trend: [{ bucket: '2026-11', count: 5 }] })
    mocks.fetchTagCooccurrence.mockResolvedValue({
      items: [{ id: 2, name: '通勤', category: 'style', count: 2 }],
      materials: 9,
    })
    const api = useTagInsight()
    const selected = ref<{ id: number } | null>(null)

    api.follow(selected as never)
    await Promise.resolve()
    await Promise.resolve()
    expect(api.trend.value).toEqual([])

    selected.value = { id: 7 }
    await vi.waitFor(() => expect(mocks.fetchTagTrend).toHaveBeenCalledWith(7, 'month'))
    expect(api.materials.value).toBe(9)

    selected.value = null
    await Promise.resolve()
    expect(api.trend.value).toEqual([])
    expect(api.cooccurrence.value).toEqual([])
  })
})

describe('长尾标签失败与边界', () => {
  it('未知分桶返回空列表；加载失败提示「加载标签失败」', async () => {
    const api = useLongTailTags()
    api.allTags.value = [tag(1, 'a', 1)] as never
    api.bucket.value = 'no_such_bucket'
    expect(api.filtered.value).toEqual([])

    mocks.fetchTagsGrouped.mockRejectedValue({ response: { data: {} } })
    await api.load()
    expect(Message.error).toHaveBeenCalledWith('加载标签失败')
    expect(api.loading.value).toBe(false)
  })

  it('切桶清空选择；选择变化映射为数字 id', () => {
    const api = useLongTailTags()
    api.selected.value = [1, 2]

    api.onBucketChange('two')
    expect(api.bucket.value).toBe('two')
    expect(api.selected.value).toEqual([])

    api.onSelectionChange(['3', 4])
    expect(api.selected.value).toEqual([3, 4])
  })

  it('合并：无精确匹配目标 / 无可合并项都只警告不发请求', async () => {
    const api = useLongTailTags()
    api.allTags.value = [tag(1, '白裙', 1), tag(2, '通勤', 2)] as never

    api.targetKeyword.value = ''
    await expect(api.doMerge()).resolves.toBe(false)
    expect(Message.warning).toHaveBeenCalledWith(
      '请先把「合并到」填成某个已存在的标签名（精确匹配）',
    )

    api.targetKeyword.value = '白裙'
    api.selected.value = [1]
    await expect(api.doMerge()).resolves.toBe(false)
    expect(Message.warning).toHaveBeenLastCalledWith('没有可合并的标签')
    expect(mocks.mergeTagsBatch).not.toHaveBeenCalled()
  })

  it('批量合并失败：提示「批量合并失败」，返回 false', async () => {
    mocks.mergeTagsBatch.mockRejectedValue({ response: { data: {} } })
    const api = useLongTailTags()
    api.allTags.value = [tag(1, '白裙', 1), tag(2, '通勤', 2)] as never
    api.targetKeyword.value = '白裙'
    api.selected.value = [2]

    await expect(api.doMerge()).resolves.toBe(false)

    expect(Message.error).toHaveBeenCalledWith('批量合并失败')
    expect(api.busy.value).toBe(false)
  })

  it('删除：空选择返回 false；失败提示「删除失败」', async () => {
    const api = useLongTailTags()
    await expect(api.doDelete()).resolves.toBe(false)
    expect(mocks.batchDeleteTags).not.toHaveBeenCalled()

    mocks.batchDeleteTags.mockRejectedValue({ response: { data: {} } })
    api.selected.value = [1]
    await expect(api.doDelete()).resolves.toBe(false)
    expect(Message.error).toHaveBeenCalledWith('删除失败')
    expect(api.busy.value).toBe(false)
  })
})
