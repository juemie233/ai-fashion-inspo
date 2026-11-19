/**
 * 穿搭大标签（`useOutfitTags`）的测试。
 *
 * 这条链路是「标签写操作」，错了会直接把脏数据写进素材：
 * - 手动添加必须按 `category='outfit'`、`source='manual'` 提交（否则大标签区不显示）；
 * - 后端 `category_mismatch` 必须明确提示「已是小标签、本次未添加」，不能只说成功；
 * - 刷新详情只在用户仍停留同一素材时写回（切换素材后旧详情不能覆盖新素材）；
 * - AI 建议只建议不入库，确认入库后建议要从列表里消失。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const mocks = vi.hoisted(() => ({
  fetchInspiration: vi.fn(),
  addTagsToInspiration: vi.fn(),
  removeTagFromInspiration: vi.fn(),
  suggestOutfitTags: vi.fn(),
  fetchTagsGrouped: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/inspirations', () => ({
  fetchInspiration: mocks.fetchInspiration,
  addTagsToInspiration: mocks.addTagsToInspiration,
  removeTagFromInspiration: mocks.removeTagFromInspiration,
  suggestOutfitTags: mocks.suggestOutfitTags,
}))

vi.mock('@/api/tags', () => ({ fetchTagsGrouped: mocks.fetchTagsGrouped }))

vi.mock('@/constants/tag', () => ({
  CATEGORY_LABELS: { outfit: '穿搭大标签', body_part: '身体部位' },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useOutfitTags } from '../useOutfitTags'
import type { InspirationDetailOut, InspirationTagOut } from '@/api/inspirations'

function tag(id: number, name: string, category = 'outfit'): InspirationTagOut {
  return { tag: { id, name, category }, confidence: 0.9, source: 'ai_generated' }
}

function makeDetail(id = 'i1', tags: InspirationTagOut[] = [tag(1, '裸足穿搭')]) {
  return { id, tags } as unknown as InspirationDetailOut
}

function emptyAddResult(overrides: Record<string, unknown> = {}) {
  return { added: [], count: 0, category_mismatch: [], ...overrides }
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function setup(initial: InspirationDetailOut | null = makeDetail()) {
  const detail = ref<InspirationDetailOut | null>(initial)
  const outfit = useOutfitTags(detail)
  return { outfit, detail }
}

function keyEnter(value: string) {
  const preventDefault = vi.fn()
  const stopPropagation = vi.fn()
  const event = {
    target: { value },
    preventDefault,
    stopPropagation,
  } as unknown as KeyboardEvent
  return { event, preventDefault, stopPropagation }
}

describe('useOutfitTags', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchInspiration.mockResolvedValue(makeDetail())
    mocks.addTagsToInspiration.mockResolvedValue(emptyAddResult())
    mocks.removeTagFromInspiration.mockResolvedValue({})
    mocks.suggestOutfitTags.mockResolvedValue({ suggestions: ['女仆装'] })
    mocks.fetchTagsGrouped.mockResolvedValue([
      {
        category: 'outfit',
        tags: [
          { id: 1, name: '裸足穿搭' },
          { id: 2, name: 'JK' },
        ],
      },
      { category: 'body_part', tags: [{ id: 3, name: '手' }] },
    ])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('大标签展示与选项加载', () => {
    it('outfitTags 只返回 category=outfit 的标签；详情为空时返回空数组', () => {
      const { outfit, detail } = setup(
        makeDetail('i1', [tag(1, '裸足穿搭', 'outfit'), tag(2, '手', 'body_part')]),
      )

      expect(outfit.outfitTags().map((t) => t.tag.name)).toEqual(['裸足穿搭'])

      detail.value = null
      expect(outfit.outfitTags()).toEqual([])
    })

    it('loadOutfitOptions：只取 outfit 分组，映射成 label/value 下拉项', async () => {
      const { outfit } = setup()

      await outfit.loadOutfitOptions()

      expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
      expect(outfit.outfitTagOptions.value).toEqual([
        { label: '裸足穿搭', value: '裸足穿搭' },
        { label: 'JK', value: 'JK' },
      ])
    })

    it('没有 outfit 分组时选项为空', async () => {
      mocks.fetchTagsGrouped.mockResolvedValue([
        { category: 'body_part', tags: [{ id: 3, name: '手' }] },
      ])
      const { outfit } = setup()

      await outfit.loadOutfitOptions()

      expect(outfit.outfitTagOptions.value).toEqual([])
    })

    it('加载选项失败静默：保留现有选项、不提示', async () => {
      const { outfit } = setup()
      outfit.outfitTagOptions.value = [{ label: '旧选项', value: '旧选项' }]
      mocks.fetchTagsGrouped.mockRejectedValue(new Error('500'))

      await outfit.loadOutfitOptions()

      expect(outfit.outfitTagOptions.value).toEqual([{ label: '旧选项', value: '旧选项' }])
      expect(mocks.message.error).not.toHaveBeenCalled()
    })
  })

  describe('手动添加大标签', () => {
    it('没有详情或没选中标签时不发请求', async () => {
      const noDetail = setup(null)
      await noDetail.outfit.addOutfitTags()
      expect(mocks.addTagsToInspiration).not.toHaveBeenCalled()

      const { outfit } = setup()
      await outfit.addOutfitTags()
      expect(mocks.addTagsToInspiration).not.toHaveBeenCalled()
    })

    it('成功：按 outfit/manual 提交、清空选择、刷新详情与选项', async () => {
      const { outfit, detail } = setup()
      outfit.outfitSelected.value = ['裸足穿搭', 'JK']

      await outfit.addOutfitTags()

      expect(mocks.addTagsToInspiration).toHaveBeenCalledWith(
        'i1',
        ['裸足穿搭', 'JK'],
        'outfit',
        'manual',
      )
      expect(outfit.outfitSelected.value).toEqual([])
      expect(mocks.message.success).toHaveBeenCalledWith('已添加大标签')
      expect(mocks.fetchInspiration).toHaveBeenCalledWith('i1')
      expect(mocks.fetchTagsGrouped).toHaveBeenCalledTimes(1)
      expect(detail.value?.id).toBe('i1')
      expect(outfit.outfitAdding.value).toBe(false)
    })

    it('后端报 category_mismatch：提示「已是小标签、本次未添加」，不提示成功', async () => {
      mocks.addTagsToInspiration.mockResolvedValue(
        emptyAddResult({
          category_mismatch: [
            {
              id: 7,
              name: 'JK',
              category: 'body_part',
              requested: 'outfit',
              already_linked: false,
            },
          ],
        }),
      )
      const { outfit } = setup()
      outfit.outfitSelected.value = ['JK']

      await outfit.addOutfitTags()

      const warning = mocks.message.warning.mock.calls[0]?.[0] as string
      expect(warning).toContain('「JK」（现为身体部位）')
      expect(warning).toContain('本次未添加')
      expect(warning).toContain('请换个名字')
      expect(mocks.message.success).not.toHaveBeenCalled()
    })

    it('失败：提示「添加失败」，保留用户已选标签以便重试', async () => {
      mocks.addTagsToInspiration.mockRejectedValue(new Error('500'))
      const { outfit } = setup()
      outfit.outfitSelected.value = ['裸足穿搭']

      await outfit.addOutfitTags()

      expect(mocks.message.error).toHaveBeenCalledWith('添加失败')
      expect(outfit.outfitSelected.value).toEqual(['裸足穿搭'])
      expect(outfit.outfitAdding.value).toBe(false)
    })

    it('刷新详情时用户已切到别的素材：旧详情不覆盖新素材', async () => {
      const { outfit, detail } = setup(makeDetail('i1'))
      outfit.outfitSelected.value = ['裸足穿搭']
      mocks.fetchInspiration.mockImplementation(async () => {
        // 请求在途时用户切走了素材
        detail.value = makeDetail('i2')
        return makeDetail('i1', [tag(9, '新标签')])
      })

      await outfit.addOutfitTags()

      expect(detail.value?.id).toBe('i2')
    })
  })

  describe('输入框回车快捷添加', () => {
    it('输入框已清空且有待添加标签：拦截回车并触发添加', async () => {
      const { outfit } = setup()
      outfit.outfitSelected.value = ['裸足穿搭']
      const { event, preventDefault, stopPropagation } = keyEnter('   ')

      outfit.onOutfitEnter(event)
      await flush()

      expect(preventDefault).toHaveBeenCalled()
      expect(stopPropagation).toHaveBeenCalled()
      expect(mocks.addTagsToInspiration).toHaveBeenCalledTimes(1)
    })

    it('输入框还有内容 / 无待添加标签 / 正在添加时不触发', async () => {
      const { outfit } = setup()
      outfit.outfitSelected.value = ['裸足穿搭']
      outfit.onOutfitEnter(keyEnter('新标签').event)

      outfit.outfitSelected.value = []
      outfit.onOutfitEnter(keyEnter('').event)

      outfit.outfitSelected.value = ['裸足穿搭']
      outfit.outfitAdding.value = true
      outfit.onOutfitEnter(keyEnter('').event)

      await flush()
      expect(mocks.addTagsToInspiration).not.toHaveBeenCalled()
    })
  })

  describe('删除与 AI 建议', () => {
    it('删除大标签：按素材 + 标签 id 调接口并刷新详情', async () => {
      const { outfit } = setup()

      await outfit.removeOutfitTag(12)

      expect(mocks.removeTagFromInspiration).toHaveBeenCalledWith('i1', 12)
      expect(mocks.fetchInspiration).toHaveBeenCalledWith('i1')
      expect(mocks.message.success).toHaveBeenCalledWith('已移除大标签')
    })

    it('删除失败提示「移除失败」；无详情时不发请求', async () => {
      const noDetail = setup(null)
      await noDetail.outfit.removeOutfitTag(1)
      expect(mocks.removeTagFromInspiration).not.toHaveBeenCalled()

      mocks.removeTagFromInspiration.mockRejectedValue(new Error('500'))
      const { outfit } = setup()
      await outfit.removeOutfitTag(1)
      expect(mocks.message.error).toHaveBeenCalledWith('移除失败')
    })

    it('AI 建议：成功写入建议列表（只建议不入库）', async () => {
      mocks.suggestOutfitTags.mockResolvedValue({ suggestions: ['女仆装', '机能风'] })
      const { outfit } = setup()

      await outfit.aiSuggestOutfitTags()

      expect(mocks.suggestOutfitTags).toHaveBeenCalledWith('i1')
      expect(mocks.addTagsToInspiration).not.toHaveBeenCalled()
      expect(outfit.aiSuggestions.value).toEqual(['女仆装', '机能风'])
      expect(outfit.aiSuggesting.value).toBe(false)
    })

    it('AI 建议为空：给 info 文案而不是静默', async () => {
      mocks.suggestOutfitTags.mockResolvedValue({ suggestions: [] })
      const { outfit } = setup()

      await outfit.aiSuggestOutfitTags()

      expect(outfit.aiSuggestions.value).toEqual([])
      expect(mocks.message.info).toHaveBeenCalledWith('AI 认为该穿搭不够有特色，未给出大标签建议')
    })

    it('AI 建议失败：透出后端 detail（无 detail 用兜底）', async () => {
      mocks.suggestOutfitTags.mockRejectedValueOnce({ response: { data: { detail: '模型超时' } } })
      const { outfit } = setup()
      await outfit.aiSuggestOutfitTags()
      expect(mocks.message.error).toHaveBeenCalledWith('模型超时')

      mocks.suggestOutfitTags.mockRejectedValueOnce({})
      await outfit.aiSuggestOutfitTags()
      expect(mocks.message.error).toHaveBeenLastCalledWith('AI 建议失败')
      expect(outfit.aiSuggesting.value).toBe(false)
    })

    it('无详情时 AI 建议直接返回', async () => {
      const { outfit } = setup(null)

      await outfit.aiSuggestOutfitTags()

      expect(mocks.suggestOutfitTags).not.toHaveBeenCalled()
    })

    it('确认单条 AI 建议：按 ai_generated 入库并从建议中移除', async () => {
      const { outfit } = setup()
      outfit.aiSuggestions.value = ['女仆装', '机能风']

      await outfit.confirmOutfitTag('女仆装')

      expect(mocks.addTagsToInspiration).toHaveBeenCalledWith(
        'i1',
        ['女仆装'],
        'outfit',
        'ai_generated',
      )
      expect(outfit.aiSuggestions.value).toEqual(['机能风'])
      expect(mocks.message.success).toHaveBeenCalledWith('已添加「女仆装」')
    })

    it('确认单条 AI 建议失败：提示「添加失败」，建议保留', async () => {
      mocks.addTagsToInspiration.mockRejectedValue(new Error('500'))
      const { outfit } = setup()
      outfit.aiSuggestions.value = ['女仆装']

      await outfit.confirmOutfitTag('女仆装')

      expect(mocks.message.error).toHaveBeenCalledWith('添加失败')
      expect(outfit.aiSuggestions.value).toEqual(['女仆装'])
    })

    it('一键入库全部建议：提交全部名称并清空建议', async () => {
      const { outfit } = setup()
      outfit.aiSuggestions.value = ['女仆装', '机能风']

      await outfit.confirmAllOutfitTags()

      expect(mocks.addTagsToInspiration).toHaveBeenCalledWith(
        'i1',
        ['女仆装', '机能风'],
        'outfit',
        'ai_generated',
      )
      expect(outfit.aiSuggestions.value).toEqual([])
      expect(mocks.message.success).toHaveBeenCalledWith('已全部入库 2 个大标签')
    })

    it('一键入库失败：提示「批量入库失败」，建议保留', async () => {
      mocks.addTagsToInspiration.mockRejectedValue(new Error('500'))
      const { outfit } = setup()
      outfit.aiSuggestions.value = ['女仆装']

      await outfit.confirmAllOutfitTags()

      expect(mocks.message.error).toHaveBeenCalledWith('批量入库失败')
      expect(outfit.aiSuggestions.value).toEqual(['女仆装'])
    })

    it('一键入库在无建议时是空操作', async () => {
      const { outfit } = setup()

      await outfit.confirmAllOutfitTags()

      expect(mocks.addTagsToInspiration).not.toHaveBeenCalled()
    })

    it('丢弃单条建议只影响该条', () => {
      const { outfit } = setup()
      outfit.aiSuggestions.value = ['女仆装', '机能风']

      outfit.dismissOutfitTag('机能风')

      expect(outfit.aiSuggestions.value).toEqual(['女仆装'])
    })
  })
})
