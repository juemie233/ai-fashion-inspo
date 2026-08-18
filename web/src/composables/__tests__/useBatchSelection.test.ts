/** useBatchSelection 批量多选 composable 单测。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

// 用 vi.hoisted 提前创建共享 message mock，避免 vi.mock 提升导致的 TDZ 问题
const message = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('naive-ui', () => ({
  useMessage: () => message,
}))

vi.mock('@/api/inspirations', () => ({
  batchFavorite: vi.fn(),
  batchTrash: vi.fn(),
  batchAddTagsToInspirations: vi.fn(),
  batchLinkBloggers: vi.fn(),
  batchUpdateInspirations: vi.fn(),
}))

import {
  batchFavorite,
  batchTrash,
  batchAddTagsToInspirations,
  batchLinkBloggers,
  batchUpdateInspirations,
} from '@/api/inspirations'
import { useBatchSelection } from '../useBatchSelection'

const mockFavorite = batchFavorite as unknown as ReturnType<typeof vi.fn>
const mockTrash = batchTrash as unknown as ReturnType<typeof vi.fn>
const mockAddTags = batchAddTagsToInspirations as unknown as ReturnType<typeof vi.fn>
const mockLinkBloggers = batchLinkBloggers as unknown as ReturnType<typeof vi.fn>
const mockUpdate = batchUpdateInspirations as unknown as ReturnType<typeof vi.fn>

describe('useBatchSelection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('进入/退出批量模式', () => {
    const b = useBatchSelection()
    b.enterBatchMode()
    expect(b.batchMode.value).toBe(true)
    b.exitBatchMode()
    expect(b.batchMode.value).toBe(false)
    expect(b.selectedCount.value).toBe(0)
  })

  it('toggleSelect 切换单个勾选', () => {
    const b = useBatchSelection()
    b.toggleSelect('a')
    expect(b.selectedIds.value.has('a')).toBe(true)
    expect(b.selectedCount.value).toBe(1)
    b.toggleSelect('a')
    expect(b.selectedIds.value.has('a')).toBe(false)
  })

  it('toggleSelectAll 全选/取消全选', () => {
    const b = useBatchSelection()
    b.toggleSelectAll(['a', 'b'])
    expect(b.selectedCount.value).toBe(2)
    b.toggleSelectAll(['a', 'b'])
    expect(b.selectedCount.value).toBe(0)
  })

  it('batchFavorite 空选择返回 0 且不请求', async () => {
    const b = useBatchSelection()
    expect(await b.batchFavorite(true)).toBe(0)
    expect(mockFavorite).not.toHaveBeenCalled()
  })

  it('batchFavorite 成功返回更新数', async () => {
    mockFavorite.mockResolvedValue(2)
    const b = useBatchSelection()
    b.toggleSelect('a')
    b.toggleSelect('b')

    expect(await b.batchFavorite(true)).toBe(2)
    expect(mockFavorite).toHaveBeenCalledWith(['a', 'b'], true)
    expect(message.success).toHaveBeenCalled()
  })

  it('batchTrash 成功（含跳过提示）', async () => {
    mockTrash.mockResolvedValue({ trashed: 2, skipped: 1 })
    const b = useBatchSelection()
    b.toggleSelect('a')
    b.toggleSelect('b')
    b.toggleSelect('c')

    expect(await b.batchTrash()).toBe(2)
    expect(message.success).toHaveBeenCalledWith('已移入垃圾桶 2 个，跳过 1 个')
  })

  it('batchAddTags 空标签不请求', async () => {
    const b = useBatchSelection()
    b.toggleSelect('a')
    await b.batchAddTags([])
    expect(mockAddTags).not.toHaveBeenCalled()
  })

  it('batchAddTags 成功传参', async () => {
    mockAddTags.mockResolvedValue({ affected: 2, not_found: 0, skipped_existing: 1 })
    const b = useBatchSelection()
    b.toggleSelect('a')
    b.toggleSelect('b')

    await b.batchAddTags(['法式'])
    expect(mockAddTags).toHaveBeenCalledWith(['a', 'b'], ['法式'], 'free', 'manual')
    expect(message.success).toHaveBeenCalled()
  })

  it('batchLinkBloggers 空选择不请求', async () => {
    const b = useBatchSelection()
    await b.batchLinkBloggers([1])
    expect(mockLinkBloggers).not.toHaveBeenCalled()
  })

  it('batchLinkBloggers 空博主列表不请求', async () => {
    const b = useBatchSelection()
    b.toggleSelect('a')
    await b.batchLinkBloggers([])
    expect(mockLinkBloggers).not.toHaveBeenCalled()
  })

  it('batchLinkBloggers 成功传参并提示', async () => {
    mockLinkBloggers.mockResolvedValue({
      linked: 3,
      affected: 2,
      not_found_count: 0,
      skipped: 1,
      message: '已关联 3 条博主关联，跳过已关联 1 条',
    })
    const b = useBatchSelection()
    b.toggleSelect('a')
    b.toggleSelect('b')

    await b.batchLinkBloggers([10, 11])
    expect(mockLinkBloggers).toHaveBeenCalledWith(['a', 'b'], [10, 11])
    expect(message.success).toHaveBeenCalledWith(expect.stringContaining('已关联 3 条博主关联'))
  })

  it('batchLinkBloggers 失败提示错误', async () => {
    mockLinkBloggers.mockRejectedValue(new Error('network'))
    const b = useBatchSelection()
    b.toggleSelect('a')

    await b.batchLinkBloggers([10])
    expect(message.error).toHaveBeenCalledWith('批量关联博主失败')
  })

  it('batchUpdate 成功返回更新数', async () => {
    mockUpdate.mockResolvedValue(2)
    const b = useBatchSelection()
    b.toggleSelect('a')
    b.toggleSelect('b')

    expect(await b.batchUpdate({ source_type: 'douyin' })).toBe(2)
    expect(mockUpdate).toHaveBeenCalledWith(['a', 'b'], { source_type: 'douyin' })
  })

  it('batchFavorite 失败返回 0 并提示错误', async () => {
    mockFavorite.mockRejectedValue(new Error('network'))
    const b = useBatchSelection()
    b.toggleSelect('a')

    expect(await b.batchFavorite(true)).toBe(0)
    expect(message.error).toHaveBeenCalledWith('批量收藏失败')
  })
})
