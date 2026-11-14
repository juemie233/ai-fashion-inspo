/**
 * f2 结果面板（`useF2Results`）的打开/关闭/错误/回滚等分支测试。
 *
 * `useF2Results.test.ts` 已覆盖筛选、加载更多、勾选与三种审查动作；本文件补：
 * - 打开/再次打开收起：收起必须清空全部面板状态（否则下次打开会闪旧数据）；
 * - 加载失败把后端 detail 落进 `error`（清单缺失/素材非法有可操作原因）；
 * - **竞态防护**：请求在途时切到别的任务，旧响应必须丢弃；
 * - 回滚本批：预览（只读）与执行（物理删除、不进垃圾桶）的提示与状态刷新；
 * - 直接调用下无打开任务时所有动作安静返回，不发请求。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useF2Results } from '../useF2Results'

function item(id: string, state = 'pending') {
  return { id, state, author: '作者', caption: '文案' }
}

function pagePayload(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      items: [item('i1')],
      total: 3,
      counts: { total: 3, live: 1, trash: 0, gone: 0, pending: 1, approved: 0, rejected: 0 },
      authors: [{ name: '作者', count: 1 }],
      task: { id: 7, status: 'success' },
      batch_id: 'b1',
      ...overrides,
    },
  }
}

describe('useF2Results（打开/错误/回滚）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue(pagePayload())
    mocks.post.mockResolvedValue({ data: { trashed: 1, skipped: 0 } })
  })

  describe('打开与收起', () => {
    it('打开任务：重置状态并按 all 拉第一页', async () => {
      const r = useF2Results()

      await r.open(7)

      expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-tasks/7/results', {
        params: { page: 1, size: expect.any(Number), state: 'all', author: undefined },
      })
      expect(r.openTaskId.value).toBe(7)
      expect(r.items.value).toHaveLength(1)
      expect(r.total.value).toBe(3)
      expect(r.counts.value).toEqual(pagePayload().data.counts)
      expect(r.authors.value).toEqual([{ name: '作者', count: 1 }])
      expect(r.batchId.value).toBe('b1')
      expect(r.task.value).toEqual({ id: 7, status: 'success' })
    })

    it('再次打开同一任务 = 收起，清空全部面板状态', async () => {
      const r = useF2Results()
      await r.open(7)
      r.selectedIds.value = new Set(['i1'])

      await r.open(7)

      expect(r.openTaskId.value).toBeNull()
      expect(r.items.value).toEqual([])
      expect(r.total.value).toBe(0)
      expect(r.authors.value).toEqual([])
      expect(r.batchId.value).toBe('')
      expect(r.task.value).toBeNull()
      expect(r.selectedIds.value.size).toBe(0)
    })

    it('加载失败把后端 detail 写进 error，loading 复位', async () => {
      mocks.get.mockRejectedValue({
        response: { data: { detail: '批次清单缺失，无法浏览' } },
      })
      const r = useF2Results()

      await r.open(7)

      expect(r.error.value).toBe('批次清单缺失，无法浏览')
      expect(r.loading.value).toBe(false)
    })

    it('竞态：切到别的任务后旧响应被丢弃', async () => {
      const r = useF2Results()
      let resolveOld: (v: unknown) => void = () => {}
      mocks.get
        .mockImplementationOnce(() => new Promise((resolve) => (resolveOld = resolve)))
        .mockResolvedValueOnce(pagePayload({ items: [item('new-task', 'approved')] }))

      const oldCall = r.open(1) // 在途
      await r.open(2) // 切到任务 2
      resolveOld(pagePayload({ items: [item('stale')] }))
      await oldCall

      expect(r.openTaskId.value).toBe(2)
      expect(r.items.value.map((i) => i.id)).toEqual(['new-task'])
    })
  })

  describe('刷新与筛选', () => {
    it('未打开任务时 reload / loadMore 不发请求', async () => {
      const r = useF2Results()

      await r.reload()
      await r.loadMore()

      expect(mocks.get).not.toHaveBeenCalled()
    })

    it('reload 清空勾选并按当前筛选回第一页', async () => {
      const r = useF2Results()
      await r.open(7)
      r.selectedIds.value = new Set(['i1'])
      r.filter.value = 'trash'

      await r.reload()

      expect(r.selectedIds.value.size).toBe(0)
      expect(mocks.get).toHaveBeenLastCalledWith(
        '/scraper/f2-tasks/7/results',
        expect.objectContaining({ params: expect.objectContaining({ page: 1, state: 'trash' }) }),
      )
    })

    it('按作者筛选时 params.author 传值；空作者传 undefined', async () => {
      const r = useF2Results()
      await r.open(7)

      await r.setAuthor('作者甲')
      expect(mocks.get).toHaveBeenLastCalledWith(
        '/scraper/f2-tasks/7/results',
        expect.objectContaining({ params: expect.objectContaining({ author: '作者甲' }) }),
      )

      await r.setAuthor('')
      expect(mocks.get).toHaveBeenLastCalledWith(
        '/scraper/f2-tasks/7/results',
        expect.objectContaining({ params: expect.objectContaining({ author: undefined }) }),
      )
    })

    it('加载更多：追加去重；没有更多时不请求', async () => {
      const r = useF2Results()
      await r.open(7) // total 3 > items 1 → hasMore
      mocks.get.mockResolvedValueOnce(pagePayload({ items: [item('i1'), item('i2')], total: 2 }))

      await r.loadMore()
      expect(r.items.value.map((i) => i.id)).toEqual(['i1', 'i2'])

      mocks.get.mockClear()
      await r.loadMore() // 已无更多
      expect(mocks.get).not.toHaveBeenCalled()
    })
  })

  describe('勾选', () => {
    it('selectAllLoaded 只选可操作项（gone 不可选），再点取消', async () => {
      mocks.get.mockResolvedValueOnce(
        pagePayload({ items: [item('live'), item('gone', 'gone')], total: 2 }),
      )
      const r = useF2Results()
      await r.open(7)

      r.selectAllLoaded()
      expect([...r.selectedIds.value]).toEqual(['live'])

      r.selectAllLoaded()
      expect(r.selectedIds.value.size).toBe(0)
    })

    it('toggleSelect 增删单个条目', async () => {
      const r = useF2Results()
      await r.open(7)

      r.toggleSelect('i1')
      expect([...r.selectedIds.value]).toEqual(['i1'])
      r.toggleSelect('i1')
      expect(r.selectedIds.value.size).toBe(0)
    })
  })

  describe('回滚本批', () => {
    it('未打开任务时预览与执行都安静返回且不发请求', async () => {
      const r = useF2Results()

      expect(await r.previewRollback()).toBeNull()
      expect(await r.rollbackBatch()).toBeNull()
      expect(mocks.get).not.toHaveBeenCalled()
      expect(mocks.post).not.toHaveBeenCalled()
    })

    it('预览：写入 rollbackInfo 并返回数据；force 透传', async () => {
      const preview = { deletable: 2, kept: 1, total: 3 }
      const r = useF2Results()
      await r.open(7)
      mocks.get.mockClear()
      mocks.get.mockResolvedValueOnce({ data: preview })

      const result = await r.previewRollback(true)

      expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-tasks/7/rollback/preview', {
        params: { force: true },
      })
      expect(result).toEqual(preview)
      expect(r.rollbackInfo.value).toEqual(preview)
      expect(r.acting.value).toBe(false)
    })

    it('预览失败提示「预览回滚失败」并返回 null', async () => {
      const r = useF2Results()
      await r.open(7)
      mocks.get.mockRejectedValueOnce(null)

      expect(await r.previewRollback()).toBeNull()
      expect(mocks.message.error).toHaveBeenCalledWith('预览回滚失败')
    })

    it('执行回滚：成功提示含保留/失败条数、清空预览并刷新当前页', async () => {
      mocks.post.mockResolvedValueOnce({ data: { deleted: 2, kept: 1, failed: 0 } })
      const r = useF2Results()
      await r.open(7)
      r.rollbackInfo.value = { deletable: 2 } as never
      mocks.get.mockClear()

      const deleted = await r.rollbackBatch()

      expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-tasks/7/rollback', null, {
        params: { force: false },
      })
      expect(deleted).toBe(2)
      expect(mocks.message.success).toHaveBeenCalledWith('已回滚 2 条，保留 1 条（已被改动过）')
      expect(r.rollbackInfo.value).toBeNull()
      expect(mocks.get).toHaveBeenCalled() // afterAction 重新拉当前页
      expect(r.acting.value).toBe(false)
    })

    it('执行回滚失败提示「回滚本批失败」并返回 null', async () => {
      mocks.post.mockRejectedValueOnce(null)
      const r = useF2Results()
      await r.open(7)

      expect(await r.rollbackBatch(true)).toBeNull()
      expect(mocks.message.error).toHaveBeenCalledWith('回滚本批失败')
      expect(r.acting.value).toBe(false)
    })
  })
})
