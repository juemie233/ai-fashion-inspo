/**
 * 相似推荐 composable（useSimilarItems）**补齐用例**：既有
 * `useSimilarItems.test.ts` 只覆盖「加载失败不能伪装成没有相似素材」，本文件补其余分支：
 *
 * - 来源标注映射（未知值原样返回）；
 * - refreshSimilar 的竞态防护（批量打标在途时切换素材，旧列表不覆盖新素材）与
 *   静默降级（后台刷新失败保持旧列表）；
 * - 收藏/移入垃圾桶的接口返回同步与失败提示；
 * - 批量模式：预填当前大标签、自动扩充下拉选项、全选/取消全选、批量添加成功后的
 *   明细文案（含不存在/已存在）与刷新、失败提示与 loading 复位。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { Ref } from 'vue'
import type { InspirationDetailOut, InspirationTagOut } from '@/api/inspirations'
import type { SimilarItemOut } from '@/api/search'
import type { OutfitTagOption } from '@/types/inspiration'

const api = vi.hoisted(() => ({
  fetchSimilar: vi.fn(),
  toggleFavorite: vi.fn(),
  moveToTrash: vi.fn(),
  batchAddTagsToInspirations: vi.fn(),
}))

vi.mock('@/api/search', () => ({ fetchSimilar: api.fetchSimilar }))
vi.mock('@/api/inspirations', () => ({
  toggleFavorite: api.toggleFavorite,
  moveToTrash: api.moveToTrash,
  batchAddTagsToInspirations: api.batchAddTagsToInspirations,
}))

import { useSimilarItems } from '../useSimilarItems'

const msgMock = () => ({ close: () => {} })

/** 构造一条相似素材（只填对比弹窗真正用到的字段） */
function makeSimilarItem(
  id: string,
  over: { is_favorite?: boolean; favorited_at?: string | null } = {},
): SimilarItemOut {
  return {
    inspiration: {
      id,
      file_path: `storage/images/${id}.jpg`,
      media_type: 'image',
      is_favorite: over.is_favorite ?? false,
      favorited_at: over.favorited_at ?? null,
      created_at: '2026-01-01T00:00:00Z',
      tags: [],
    } as unknown as SimilarItemOut['inspiration'],
    similarity: 0.9,
    shared_tags: 2,
    match_source: 'tag',
  }
}

/** 构造当前素材详情（只用到 id） */
function makeDetail(id: string): InspirationDetailOut {
  return { id } as unknown as InspirationDetailOut
}

/** 构造穿搭大标签（只用到 tag.name） */
function makeOutfitTag(name: string): InspirationTagOut {
  return {
    tag: { id: 1, name, category: 'outfit' },
    confidence: 1,
    source: 'manual',
  }
}

interface SetupOptions {
  detailId?: string | null
  outfitTags?: string[]
  optionValues?: string[]
  seq?: number
}

function setup(options: SetupOptions = {}) {
  const detail: Ref<InspirationDetailOut | null> = ref(
    options.detailId === null ? null : makeDetail(options.detailId ?? 'insp-1'),
  )
  const outfitTagOptions = ref<OutfitTagOption[]>(
    (options.optionValues ?? []).map((value) => ({ label: value, value })),
  )
  const tags = (options.outfitTags ?? []).map(makeOutfitTag)
  const seq = options.seq ?? 1
  const state = useSimilarItems(
    detail,
    outfitTagOptions,
    () => tags,
    (s) => s === seq,
  )
  return { detail, outfitTagOptions, ...state }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(Message, 'success').mockImplementation(msgMock)
  vi.spyOn(Message, 'error').mockImplementation(msgMock)
})

describe('similarSourceLabel 来源标注', () => {
  it('三种已知来源有中文标注，未知来源原样返回', () => {
    const { similarSourceLabel } = setup()

    expect(similarSourceLabel('visual')).toBe('视觉相似')
    expect(similarSourceLabel('tag')).toBe('标签相似')
    expect(similarSourceLabel('hybrid')).toBe('视觉+标签')
    expect(similarSourceLabel('future_source')).toBe('future_source')
    expect(similarSourceLabel('')).toBe('')
  })
})

describe('loadSimilar 加载中状态', () => {
  it('请求在途时 similarLoading=true，完成后复位', async () => {
    let resolveSimilar!: (value: unknown) => void
    api.fetchSimilar.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveSimilar = resolve
        }),
    )
    const { similarLoading, loadSimilar } = setup()

    const pending = loadSimilar('insp-1', 1)
    expect(similarLoading.value).toBe(true)

    resolveSimilar({ similar: [] })
    await pending
    expect(similarLoading.value).toBe(false)
  })

  it('过期响应的失败不写状态（序号不匹配时 catch 直接返回）', async () => {
    api.fetchSimilar.mockRejectedValueOnce(new Error('500'))
    const { similarFailed, similarItems, loadSimilar } = setup({ seq: 1 })

    await loadSimilar('insp-5', 2) // 2 !== 1 → 过期响应

    expect(similarFailed.value).toBe(false)
    expect(similarItems.value).toEqual([])
  })
})

describe('refreshSimilar 后台刷新', () => {
  it('成功：覆盖列表并复位失败标记', async () => {
    api.fetchSimilar.mockResolvedValueOnce({ similar: [makeSimilarItem('c1')] })
    const { similarItems, similarFailed, refreshSimilar } = setup()

    await refreshSimilar('insp-1')

    expect(api.fetchSimilar).toHaveBeenCalledWith('insp-1', 10)
    expect(similarItems.value.map((it) => it.inspiration.id)).toEqual(['c1'])
    expect(similarFailed.value).toBe(false)
  })

  it('批量打标在途时切换了素材：旧素材的响应不覆盖当前列表', async () => {
    api.fetchSimilar.mockResolvedValueOnce({ similar: [makeSimilarItem('old')] })
    const { similarItems, detail, refreshSimilar } = setup({ detailId: 'insp-1' })

    // 请求返回前切到另一条素材
    detail.value = makeDetail('insp-2')
    await refreshSimilar('insp-1')

    expect(similarItems.value).toEqual([])
  })

  it('刷新失败：静默降级，保留旧列表且不翻转 failed', async () => {
    api.fetchSimilar.mockResolvedValueOnce({ similar: [makeSimilarItem('keep')] })
    const { similarItems, similarFailed, refreshSimilar } = setup()
    await refreshSimilar('insp-1')

    api.fetchSimilar.mockRejectedValueOnce(new Error('500'))
    similarFailed.value = false
    await refreshSimilar('insp-1')

    expect(similarItems.value.map((it) => it.inspiration.id)).toEqual(['keep'])
    expect(similarFailed.value).toBe(false)
  })
})

describe('toggleFavoriteSimilar 收藏/取消收藏', () => {
  it('找到条目：按取反后的状态调用接口，并用响应同步收藏时间', async () => {
    api.fetchSimilar.mockResolvedValueOnce({
      similar: [makeSimilarItem('c1', { is_favorite: false })],
    })
    const { similarItems, loadSimilar, toggleFavoriteSimilar } = setup()
    await loadSimilar('insp-1', 1)

    api.toggleFavorite.mockResolvedValueOnce({
      is_favorite: true,
      favorited_at: '2026-11-20T12:00:00Z',
    })

    await toggleFavoriteSimilar('c1')

    expect(api.toggleFavorite).toHaveBeenCalledWith('c1', true)
    expect(similarItems.value[0]?.inspiration.is_favorite).toBe(true)
    expect(similarItems.value[0]?.inspiration.favorited_at).toBe('2026-11-20T12:00:00Z')
  })

  it('列表里没有该条目时不发请求', async () => {
    const { toggleFavoriteSimilar } = setup()

    await toggleFavoriteSimilar('missing')

    expect(api.toggleFavorite).not.toHaveBeenCalled()
  })

  it('接口失败：提示「操作失败」', async () => {
    api.fetchSimilar.mockResolvedValueOnce({ similar: [makeSimilarItem('c1')] })
    const { loadSimilar, toggleFavoriteSimilar } = setup()
    await loadSimilar('insp-1', 1)
    api.toggleFavorite.mockRejectedValueOnce(new Error('500'))

    await toggleFavoriteSimilar('c1')

    expect(Message.error).toHaveBeenCalledWith('操作失败')
  })
})

describe('deleteSimilar 移入垃圾桶', () => {
  it('成功：从相似列表移除并提示', async () => {
    api.fetchSimilar.mockResolvedValueOnce({
      similar: [makeSimilarItem('c1'), makeSimilarItem('c2')],
    })
    const { similarItems, loadSimilar, deleteSimilar } = setup()
    await loadSimilar('insp-1', 1)
    api.moveToTrash.mockResolvedValueOnce({})

    await deleteSimilar('c1')

    expect(api.moveToTrash).toHaveBeenCalledWith('c1')
    expect(similarItems.value.map((it) => it.inspiration.id)).toEqual(['c2'])
    expect(Message.success).toHaveBeenCalledWith('已移入垃圾桶')
  })

  it('失败：提示且列表不变', async () => {
    api.fetchSimilar.mockResolvedValueOnce({ similar: [makeSimilarItem('c1')] })
    const { similarItems, loadSimilar, deleteSimilar } = setup()
    await loadSimilar('insp-1', 1)
    api.moveToTrash.mockRejectedValueOnce(new Error('500'))

    await deleteSimilar('c1')

    expect(similarItems.value.map((it) => it.inspiration.id)).toEqual(['c1'])
    expect(Message.error).toHaveBeenCalledWith('操作失败')
  })
})

describe('批量模式', () => {
  it('enterBatchMode：预填当前素材大标签，并把未进下拉的标签补进选项', () => {
    const { batchMode, batchSelectedIds, batchTagNames, outfitTagOptions, enterBatchMode } = setup({
      outfitTags: ['白裙', '通勤'],
      optionValues: ['白裙'],
    })
    batchSelectedIds.value = ['stale']

    enterBatchMode()

    expect(batchMode.value).toBe(true)
    expect(batchSelectedIds.value).toEqual([])
    expect(batchTagNames.value).toEqual(['白裙', '通勤'])
    // 「通勤」不在原选项里 → 自动补一条，保证批量下拉能选到
    expect(outfitTagOptions.value).toEqual([
      { label: '白裙', value: '白裙' },
      { label: '通勤', value: '通勤' },
    ])
  })

  it('exitBatchMode：清空模式、勾选与标签', () => {
    const { batchMode, batchSelectedIds, batchTagNames, enterBatchMode, exitBatchMode } = setup({
      outfitTags: ['白裙'],
    })
    enterBatchMode()

    exitBatchMode()

    expect(batchMode.value).toBe(false)
    expect(batchSelectedIds.value).toEqual([])
    expect(batchTagNames.value).toEqual([])
  })

  it('toggleSelectSimilar：未选则加、已选则删', () => {
    const { batchSelectedIds, toggleSelectSimilar } = setup()

    toggleSelectSimilar('c1')
    toggleSelectSimilar('c2')
    expect(batchSelectedIds.value).toEqual(['c1', 'c2'])

    toggleSelectSimilar('c1')
    expect(batchSelectedIds.value).toEqual(['c2'])
  })

  it('toggleSelectAll：未全选则全选，已全选则清空；空列表清空', async () => {
    api.fetchSimilar.mockResolvedValueOnce({
      similar: [makeSimilarItem('c1'), makeSimilarItem('c2')],
    })
    const { batchSelectedIds, loadSimilar, toggleSelectAll } = setup()
    await loadSimilar('insp-1', 1)

    toggleSelectAll()
    expect(batchSelectedIds.value).toEqual(['c1', 'c2'])

    toggleSelectAll()
    expect(batchSelectedIds.value).toEqual([])

    batchSelectedIds.value = ['x']
    toggleSelectAll() // 列表非空但未全选 → 全选
    expect(batchSelectedIds.value).toEqual(['c1', 'c2'])
  })
})

describe('batchAddOutfitTags 批量添加大标签', () => {
  it('未勾选或未选标签时直接返回，不发请求', async () => {
    const first = setup({ outfitTags: ['白裙'] })
    await first.batchAddOutfitTags()
    expect(api.batchAddTagsToInspirations).not.toHaveBeenCalled()
    expect(first.batchAdding.value).toBe(false)

    const second = setup({ outfitTags: [] })
    second.enterBatchMode()
    await second.batchAddOutfitTags()
    expect(api.batchAddTagsToInspirations).not.toHaveBeenCalled()
    expect(second.batchAdding.value).toBe(false)
  })

  it('成功：按勾选与标签调用接口，明细提示区分新增/不存在/已存在，并刷新相似列表', async () => {
    api.fetchSimilar.mockResolvedValue({ similar: [makeSimilarItem('c1')] })
    const {
      detail,
      batchSelectedIds,
      batchTagNames,
      batchMode,
      enterBatchMode,
      batchAddOutfitTags,
    } = setup({ detailId: 'insp-1', outfitTags: ['白裙', '通勤'] })
    enterBatchMode()
    batchSelectedIds.value = ['c1', 'c2']
    expect(detail.value?.id).toBe('insp-1')

    api.batchAddTagsToInspirations.mockResolvedValueOnce({
      added: 2,
      affected: 3,
      skipped: 1,
      not_found: 1,
      skipped_existing: 2,
      missing_ids: [],
    })
    api.fetchSimilar.mockClear()

    await batchAddOutfitTags()

    expect(api.batchAddTagsToInspirations).toHaveBeenCalledWith(
      ['c1', 'c2'],
      ['白裙', '通勤'],
      'outfit',
      'manual',
    )
    expect(Message.success).toHaveBeenCalledWith(
      '已为 3 个相似素材添加大标签，1 个素材不存在，2 条关联已存在',
    )
    expect(batchMode.value).toBe(false)
    // 批量成功后刷新相似列表（标签/相似度可能变化）
    expect(api.fetchSimilar).toHaveBeenCalledWith('insp-1', 10)
    expect(batchTagNames.value).toEqual([])
  })

  it('成功但没有明细异常时只提示新增数；detail 为空时不刷新', async () => {
    const { batchSelectedIds, enterBatchMode, batchAddOutfitTags } = setup({
      detailId: null,
      outfitTags: ['白裙'],
    })
    enterBatchMode()
    batchSelectedIds.value = ['c1']
    api.batchAddTagsToInspirations.mockResolvedValueOnce({
      added: 1,
      affected: 1,
      skipped: 0,
      not_found: 0,
      skipped_existing: 0,
      missing_ids: [],
    })
    api.fetchSimilar.mockClear()

    await batchAddOutfitTags()

    expect(Message.success).toHaveBeenCalledWith('已为 1 个相似素材添加大标签')
    expect(api.fetchSimilar).not.toHaveBeenCalled()
  })

  it('失败：提示「批量添加失败」，不退出批量模式，loading 复位', async () => {
    const { batchSelectedIds, batchMode, batchAdding, enterBatchMode, batchAddOutfitTags } = setup({
      outfitTags: ['白裙'],
    })
    enterBatchMode()
    batchSelectedIds.value = ['c1']
    api.batchAddTagsToInspirations.mockRejectedValueOnce(new Error('500'))

    await batchAddOutfitTags()

    expect(Message.error).toHaveBeenCalledWith('批量添加失败')
    expect(batchMode.value).toBe(true)
    expect(batchAdding.value).toBe(false)
  })
})
