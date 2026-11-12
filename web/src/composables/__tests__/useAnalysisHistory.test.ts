/**
 * AI 分析历史面板逻辑（`useAnalysisHistory`）的测试。
 *
 * 这套逻辑决定「分析历史列表」能不能正确筛选、翻页、批量清理失败记录，以及最危险的
 * 一类操作——**从历史里把素材移入垃圾桶**（软删除，但用户以为只是删记录）。
 * 以前它 0% 覆盖，出错时表现为：筛选不生效、翻页回第一页、批量操作按钮没反应。
 *
 * 重点钉住的语义：
 * 1. **筛选/翻页/排序都要把页码重置为 1 并清空选中**（否则会出现「第 3 页筛选后空列表」）；
 * 2. **过期响应丢弃**：连续切筛选时，先发的请求晚回来不能覆盖新结果；
 * 3. **主动取消不报错**：切换筛选触发的 abort 是正常流程，不能弹「加载历史失败」；
 * 4. 批量操作在**没有选中项**时直接返回（不发请求）；
 * 5. 移入垃圾桶 / 批量删除 / 清空失败会触发队列与活动分析的刷新回调（统计要跟着变）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  // delete 是保留字，用 del 占位
  del: vi.fn(),
  moveToTrash: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  requestAndNotify: vi.fn(),
  loadQueue: vi.fn(),
  loadActiveAnalyses: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.del },
}))

vi.mock('@/api/inspirations', async () => {
  const actual = await vi.importActual<typeof import('@/api/inspirations')>('@/api/inspirations')
  return { ...actual, moveToTrash: mocks.moveToTrash }
})

vi.mock('@/composables/useNotification', () => ({
  useNotification: () => ({ requestAndNotify: mocks.requestAndNotify }),
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useAnalysisHistory } from '../useAnalysisHistory'

function historyItem(overrides: Record<string, unknown> = {}) {
  return { id: 1, inspiration_id: 'insp-1', status: 'success', ...overrides }
}

function page(items: unknown[] = [historyItem()], total = items.length) {
  return { data: { items, total } }
}

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('useAnalysisHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue(page())
    mocks.post.mockResolvedValue({ data: { message: 'ok' } })
    mocks.del.mockResolvedValue({ data: { message: 'ok' } })
    mocks.moveToTrash.mockResolvedValue(undefined)
  })

  function setup() {
    return useAnalysisHistory({
      loadQueue: mocks.loadQueue,
      loadActiveAnalyses: mocks.loadActiveAnalyses,
    })
  }

  describe('加载与筛选', () => {
    it('默认只带分页参数，并写入列表与总数', async () => {
      const h = setup()
      mocks.get.mockResolvedValue(page([historyItem({ id: 7 })], 42))

      await h.loadHistory()

      expect(mocks.get).toHaveBeenCalledWith('/ai/history', {
        params: { page: 1, size: 20 },
        signal: expect.any(AbortSignal),
      })
      expect(h.history.value).toHaveLength(1)
      expect(h.historyTotal.value).toBe(42)
      expect(h.historyLoading.value).toBe(false)
    })

    it('所有筛选条件都转成查询参数（日期截成 YYYY-MM-DD）', async () => {
      const h = setup()
      h.historyFilter.value = 'failed'
      h.historyModelFilter.value = 'qwen3-vl:8b-instruct'
      h.historyPromptFilter.value = 'abc123'
      h.historySearchId.value = '  insp-42  '
      h.historyStartDate.value = new Date(2026, 0, 5).getTime()
      h.historyEndDate.value = new Date(2026, 0, 31).getTime()
      h.historySortBy.value = 'time_desc'

      await h.loadHistory()

      expect(mocks.get).toHaveBeenCalledWith(
        '/ai/history',
        expect.objectContaining({
          params: {
            page: 1,
            size: 20,
            status: 'failed',
            model_name: 'qwen3-vl:8b-instruct',
            prompt_version: 'abc123',
            inspiration_id: 'insp-42', // 去首尾空白
            start_date: '2026-01-05',
            end_date: '2026-01-31',
            sort_by: 'time_desc',
          },
        }),
      )
    })

    it.each([
      ['filterHistory', 'historyFilter', 'failed'],
      ['filterByModel', 'historyModelFilter', 'm1'],
      ['filterByPrompt', 'historyPromptFilter', 'p1'],
    ] as const)('%s 会把页码重置为 1 并清空选中', async (method, field, value) => {
      const h = setup()
      h.historyPage.value = 3
      h.selectedHistoryIds.value = new Set([1, 2])

      h[method](value)

      expect(h[field].value).toBe(value)
      expect(h.historyPage.value).toBe(1)
      expect(h.selectedHistoryIds.value.size).toBe(0)
      await flush()
    })

    it('时间范围与耗时排序同样重置页码', async () => {
      const h = setup()
      h.historyPage.value = 2

      h.filterByDate(new Date(2026, 0, 1).getTime(), null)
      expect(h.historyStartDate.value).not.toBeNull()
      expect(h.historyPage.value).toBe(1)

      h.historyPage.value = 2
      h.sortByTime('time_asc')
      expect(h.historySortBy.value).toBe('time_asc')
      expect(h.historyPage.value).toBe(1)
      await flush()
    })

    it('翻页只改页码并重新加载', async () => {
      const h = setup()
      h.onHistoryPageChange(4)
      expect(h.historyPage.value).toBe(4)
      await flush()
      expect(mocks.get).toHaveBeenCalledWith(
        '/ai/history',
        expect.objectContaining({ params: expect.objectContaining({ page: 4 }) }),
      )
    })

    it('加载失败提示错误，loading 回落', async () => {
      const h = setup()
      mocks.get.mockRejectedValue(new Error('500'))

      await h.loadHistory()

      expect(mocks.message.error).toHaveBeenCalledWith('加载历史失败')
      expect(h.historyLoading.value).toBe(false)
    })

    it('主动取消（切换筛选）不弹错误提示', async () => {
      const h = setup()
      mocks.get.mockRejectedValue(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' }))

      await h.loadHistory()

      expect(mocks.message.error).not.toHaveBeenCalled()
      expect(h.historyLoading.value).toBe(false)
    })

    it('过期响应不覆盖新结果（先发晚回）', async () => {
      const h = setup()
      let resolveFirst: (v: unknown) => void = () => {}
      mocks.get
        .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
        .mockResolvedValueOnce(page([historyItem({ id: 99 })], 1))

      const first = h.loadHistory() // 旧请求（挂起）
      await h.loadHistory() // 新请求先返回
      resolveFirst(page([historyItem({ id: 1 })], 1)) // 旧请求后返回
      await first

      expect(h.history.value).toHaveLength(1)
      expect((h.history.value[0] as { id: number }).id).toBe(99)
    })

    it('abortHistoryRequest 中止在途请求', () => {
      const h = setup()
      h.loadHistory()
      expect(() => h.abortHistoryRequest()).not.toThrow()
      h.abortHistoryRequest() // 无在途请求时也不能崩
    })
  })

  describe('下拉选项', () => {
    it('加载模型名与提示词版本', async () => {
      const h = setup()
      mocks.get
        .mockResolvedValueOnce({ data: { models: ['m1', 'm2'] } })
        .mockResolvedValueOnce({ data: { versions: [{ prompt_version: 'p1', count: 3 }] } })

      await h.loadModelNames()
      await h.loadPromptVersions()

      expect(h.historyModelNames.value).toEqual(['m1', 'm2'])
      expect(h.historyPromptVersions.value).toEqual([{ prompt_version: 'p1', count: 3 }])
    })

    it('下拉加载失败静默（不影响主列表）', async () => {
      const h = setup()
      mocks.get.mockRejectedValue(new Error('500'))

      await h.loadModelNames()
      await h.loadPromptVersions()

      expect(mocks.message.error).not.toHaveBeenCalled()
      expect(h.historyModelNames.value).toEqual([])
    })
  })

  describe('选中与批量操作', () => {
    it('单选切换、全选、再点一次取消全选', async () => {
      const h = setup()
      h.history.value = [historyItem({ id: 1 }), historyItem({ id: 2 })] as never

      h.toggleSelectHistory(1)
      expect([...h.selectedHistoryIds.value]).toEqual([1])
      h.toggleSelectHistory(1) // 再点取消
      expect(h.selectedHistoryIds.value.size).toBe(0)

      h.selectAllHistory()
      expect([...h.selectedHistoryIds.value].sort()).toEqual([1, 2])
      h.selectAllHistory() // 已全选 → 取消
      expect(h.selectedHistoryIds.value.size).toBe(0)
    })

    it('空列表时全选不会选中任何项', () => {
      const h = setup()
      h.history.value = []
      h.selectAllHistory()
      expect(h.selectedHistoryIds.value.size).toBe(0)
    })

    it('没有选中项时批量删除/重试直接返回（不发请求）', async () => {
      const h = setup()
      await h.batchDeleteHistory()
      await h.batchRetryHistory()
      expect(mocks.post).not.toHaveBeenCalled()
    })

    it('批量删除成功：清空选中并刷新队列统计', async () => {
      const h = setup()
      h.selectedHistoryIds.value = new Set([1, 2])

      await h.batchDeleteHistory()

      expect(mocks.post).toHaveBeenCalledWith('/ai/history/batch-delete', { ids: [1, 2] })
      expect(mocks.message.success).toHaveBeenCalledWith('已删除 2 条记录')
      expect(h.selectedHistoryIds.value.size).toBe(0)
      expect(mocks.loadQueue).toHaveBeenCalled()
    })

    it('批量重试成功：提示 + 桌面通知 + 刷新活动分析', async () => {
      const h = setup()
      h.selectedHistoryIds.value = new Set([3])

      await h.batchRetryHistory()

      expect(mocks.post).toHaveBeenCalledWith('/ai/history/batch-retry', { ids: [3] })
      expect(mocks.requestAndNotify).toHaveBeenCalledWith(
        '批量重试已启动',
        expect.objectContaining({ tag: 'batch-retry' }),
      )
      expect(mocks.loadActiveAnalyses).toHaveBeenCalled()
    })

    it('批量操作失败给出对应错误文案', async () => {
      const h = setup()
      h.selectedHistoryIds.value = new Set([1])
      // 用「无 detail 无 message」的异常，验证回退到操作名文案
      mocks.post.mockRejectedValue(null)

      await h.batchDeleteHistory()
      await h.batchRetryHistory()

      expect(mocks.message.error).toHaveBeenNthCalledWith(
        1,
        expect.stringContaining('批量删除失败'),
      )
      expect(mocks.message.error).toHaveBeenNthCalledWith(
        2,
        expect.stringContaining('批量重试失败'),
      )
    })

    it('单条删除/应用标签/移入垃圾桶成功后刷新', async () => {
      const h = setup()

      await h.deleteLog(5)
      await h.applyLogToMaterial(6)
      await h.deleteInspirationFromHistory('insp-9', '质量差')

      expect(mocks.del).toHaveBeenCalledWith('/ai/history/5')
      expect(mocks.post).toHaveBeenCalledWith('/ai/history/6/apply')
      expect(mocks.moveToTrash).toHaveBeenCalledWith('insp-9', '质量差')
      expect(mocks.message.success).toHaveBeenCalledWith('分析记录已删除')
      expect(mocks.message.success).toHaveBeenCalledWith('素材已移入垃圾桶（可在垃圾桶恢复）')
      expect(mocks.loadQueue).toHaveBeenCalled()
      expect(mocks.loadActiveAnalyses).toHaveBeenCalled()
    })

    it('单条操作失败转为可读错误', async () => {
      const h = setup()
      mocks.del.mockRejectedValue(new Error('500'))
      mocks.post.mockRejectedValue(new Error('500'))
      mocks.moveToTrash.mockRejectedValue(new Error('500'))

      await h.deleteLog(1)
      await h.applyLogToMaterial(1)
      await h.deleteInspirationFromHistory('insp-1')

      expect(mocks.message.error).toHaveBeenCalledTimes(3)
    })

    it('一键重试失败记录：期间 retryingAll 为 true，结束后复位', async () => {
      const h = setup()
      const promise = h.retryAllFailed()
      expect(h.retryingAll.value).toBe(true)
      await promise

      expect(h.retryingAll.value).toBe(false)
      expect(mocks.post).toHaveBeenCalledWith('/ai/retry-all-failed')
      expect(mocks.requestAndNotify).toHaveBeenCalledWith(
        '失败重试已启动',
        expect.objectContaining({ tag: 'retry-failed' }),
      )
      expect(mocks.loadQueue).toHaveBeenCalled()
      expect(mocks.loadActiveAnalyses).toHaveBeenCalled()
    })

    it('清空失败记录：期间 clearingFailed 为 true，失败也复位', async () => {
      const h = setup()
      const promise = h.deleteAllFailed()
      expect(h.clearingFailed.value).toBe(true)
      await promise

      expect(h.clearingFailed.value).toBe(false)
      expect(mocks.del).toHaveBeenCalledWith('/ai/history/failed/all')
      expect(mocks.loadQueue).toHaveBeenCalled()

      mocks.del.mockRejectedValue(null)
      await h.deleteAllFailed()
      expect(h.clearingFailed.value).toBe(false)
      expect(mocks.message.error).toHaveBeenCalledWith(expect.stringContaining('清空失败'))
    })
  })

  describe('导出 CSV', () => {
    it('按当前筛选导出并触发下载', async () => {
      const h = setup()
      h.historyFilter.value = 'failed'
      h.historySearchId.value = 'insp-1'
      const blob = new Blob(['a,b'], { type: 'text/csv' })
      mocks.get.mockResolvedValue({ data: blob })
      const createObjectURL = vi.fn(() => 'blob:mock')
      const revokeObjectURL = vi.fn()
      vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
      const click = vi.fn()
      const realCreate = document.createElement.bind(document)
      const createSpy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        const el = realCreate(tag) as HTMLAnchorElement
        if (tag === 'a') el.click = click
        return el
      })

      try {
        await h.exportHistoryCsv()

        expect(mocks.get).toHaveBeenCalledWith('/ai/history/export', {
          params: { status: 'failed', inspiration_id: 'insp-1' },
          responseType: 'blob',
        })
        expect(createObjectURL).toHaveBeenCalled()
        expect(click).toHaveBeenCalled()
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')
        expect(mocks.message.success).toHaveBeenCalledWith('已导出 CSV')
      } finally {
        createSpy.mockRestore()
        vi.unstubAllGlobals()
      }
    })

    it('导出失败提示错误', async () => {
      const h = setup()
      mocks.get.mockRejectedValue(null)

      await h.exportHistoryCsv()

      expect(mocks.message.error).toHaveBeenCalledWith(expect.stringContaining('导出失败'))
    })
  })
})
