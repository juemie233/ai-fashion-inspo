/**
 * f2 结果浏览与审查 composable（useF2Results）测试。
 *
 * 关注：① 打开任务拉第一页、切筛选/作者回到第一页重新拉；② 加载更多追加去重；
 * ③ 勾选只作用于可操作项（已彻底删除的不能选）；④ 三个审查动作各自提交正确的
 * ID 子集（移入垃圾桶只提交在库项、还原只提交垃圾桶项、彻底删除提交全部），
 * 且动作后按当前筛选重新拉取并清空勾选。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useF2Results, type F2ResultItem } from '../useF2Results'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

function makeItem(over: Partial<F2ResultItem> = {}): F2ResultItem {
  return {
    id: 'i1',
    state: 'pending',
    quality_status: 'pending',
    media_type: 'image',
    caption: '正文',
    author: '里香',
    hashtags: ['jk'],
    file_path: 'images/2026-09/a.webp',
    thumbnail_path: 'thumbnails/2026-09/a.jpg',
    is_favorite: false,
    trash_reason: null,
    source_platform_id: 'f2:abc#image1',
    created_at: '2026-09-15 01:41:19',
    ...over,
  }
}

const COUNTS = {
  total: 3,
  live: 1,
  trash: 1,
  gone: 1,
  pending: 1,
  approved: 0,
  rejected: 0,
}

function respond(items: F2ResultItem[], over: Record<string, unknown> = {}) {
  return {
    data: {
      task: {
        id: 338,
        status: 'success',
        created_at: '2026-09-15 01:41:19',
        updated_at: '2026-09-15 01:47:33',
        error: null,
        imported: 3,
        failed: 0,
        fetch_ok: 2,
        fetch_total: 2,
      },
      batch_id: 'f2-20260915-014733-cacf',
      items: [...items],
      total: items.length,
      page: 1,
      size: 60,
      counts: COUNTS,
      authors: [{ name: '里香', count: 2 }],
      has_batch: true,
      ...over,
    },
  }
}

const THREE = [
  makeItem({ id: 'live', state: 'pending' }),
  makeItem({ id: 'trashed', state: 'trash', trash_reason: '质量差' }),
  makeItem({ id: 'gone', state: 'gone', file_path: null, thumbnail_path: null }),
]

/** 动作成功后面板会按当前筛选重新拉取：统一返回同一份数据 */
function resetGet() {
  mocks.get.mockResolvedValue(respond(THREE))
}

describe('useF2Results', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('打开任务：按 task_id 拉第一页并解析计数/作者/任务信息', async () => {
    mocks.get.mockResolvedValue(respond(THREE))
    const r = useF2Results()

    await r.open(338)

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-tasks/338/results', {
      params: { page: 1, size: 60, state: 'all', author: undefined },
    })
    expect(r.openTaskId.value).toBe(338)
    expect(r.items.value).toHaveLength(3)
    expect(r.counts.value.gone).toBe(1)
    expect(r.batchId.value).toBe('f2-20260915-014733-cacf')
    expect(r.task.value?.imported).toBe(3)
    expect(r.authors.value).toEqual([{ name: '里香', count: 2 }])
  })

  it('切筛选/作者：回到第一页重新拉取并清空勾选', async () => {
    mocks.get.mockResolvedValue(respond(THREE))
    const r = useF2Results()
    await r.open(338)
    r.toggleSelect('live')

    await r.setFilter('trash')
    expect(mocks.get).toHaveBeenLastCalledWith('/scraper/f2-tasks/338/results', {
      params: { page: 1, size: 60, state: 'trash', author: undefined },
    })
    expect(r.selectedIds.value.size).toBe(0)

    await r.setAuthor('里香')
    expect(mocks.get).toHaveBeenLastCalledWith('/scraper/f2-tasks/338/results', {
      params: { page: 1, size: 60, state: 'trash', author: '里香' },
    })
  })

  it('加载更多：追加下一页并跳过重复 ID', async () => {
    mocks.get.mockResolvedValueOnce(respond(THREE, { total: 5 }))
    const r = useF2Results()
    await r.open(338)
    mocks.get.mockResolvedValueOnce(respond([THREE[0], makeItem({ id: 'i9' })], { total: 5 }))

    await r.loadMore()

    expect(mocks.get).toHaveBeenLastCalledWith('/scraper/f2-tasks/338/results', {
      params: { page: 2, size: 60, state: 'all', author: undefined },
    })
    expect(r.items.value.map((i) => i.id)).toEqual(['live', 'trashed', 'gone', 'i9'])
  })

  it('全选只作用于可操作项：已彻底删除的不进勾选', async () => {
    mocks.get.mockResolvedValue(respond(THREE))
    const r = useF2Results()
    await r.open(338)

    r.selectAllLoaded()

    expect([...r.selectedIds.value].sort()).toEqual(['live', 'trashed'])
  })

  it('移入垃圾桶：只提交在库项；全是垃圾桶项时提示且不发请求', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue(respond(THREE))
    mocks.post.mockResolvedValue({ data: { requested: 1, trashed: 1, skipped: 0 } })
    const r = useF2Results()
    await r.open(338)
    resetGet()

    r.toggleSelect('live')
    r.toggleSelect('gone')
    await r.trashSelected('质量差')

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-tasks/338/results/trash', {
      ids: ['live'],
      reason: '质量差',
    })
    expect(r.selectedIds.value.size).toBe(0)
    expect(success).toHaveBeenCalled()

    // 只选中垃圾桶里的素材（非 gone）→ 无可移入对象
    mocks.post.mockClear()
    r.toggleSelect('trashed')
    await r.trashSelected('质量差')
    expect(mocks.post).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalled()
    success.mockRestore()
    warn.mockRestore()
  })

  it('还原与彻底删除：各自提交正确的 ID 子集', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue(respond(THREE))
    mocks.post.mockResolvedValue({ data: { requested: 1, restored: 1, skipped: 0 } })
    const r = useF2Results()
    await r.open(338)
    resetGet()

    r.toggleSelect('live')
    r.toggleSelect('trashed')
    await r.restoreSelected()
    expect(mocks.post).toHaveBeenLastCalledWith('/scraper/f2-tasks/338/results/restore', {
      ids: ['trashed'],
    })

    mocks.post.mockResolvedValue({ data: { requested: 3, count: 3, task_id: 350 } })
    r.selectAllLoaded()
    await r.deleteSelected()
    expect(mocks.post).toHaveBeenLastCalledWith('/scraper/f2-tasks/338/results/delete', {
      ids: ['live', 'trashed'],
    })
    expect(success).toHaveBeenCalledWith(expect.stringContaining('#350'))
    success.mockRestore()
  })

  it('再次打开同一任务 = 收起（清空面板状态）', async () => {
    mocks.get.mockResolvedValue(respond(THREE))
    const r = useF2Results()
    await r.open(338)

    await r.open(338)

    expect(r.openTaskId.value).toBeNull()
    expect(r.items.value).toEqual([])
    expect(r.counts.value.total).toBe(0)
  })
})
