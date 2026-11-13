/**
 * 采集结果预览（`useScraperResults`）的测试。
 *
 * 结果预览是「一次采集采到了什么」的唯一直观入口，且支持跨页勾选后批量移入垃圾桶。
 * 原覆盖率 0%，最容易错的几个点：
 * 1. **追加去重**：加载更多时后端可能返回重叠条目，重复 push 会让列表出现同一张图两次；
 * 2. **竞态丢弃**：加载更多在途时切到别的任务，旧响应不能覆盖新任务的列表；
 * 3. **删除后的分页收敛**：全部删完要收起面板；当前页删空但还有剩余要回到第一页重拉；
 * 4. **跨页勾选保留**：加载更多不能清空已勾选（用户可能勾了几页再一起删）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  refreshTasks: vi.fn(),
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

import { useScraperResults } from '../useScraperResults'

function page(items: unknown[], total: number) {
  return { data: { items, total } }
}

function setup() {
  return useScraperResults({ refreshTasks: mocks.refreshTasks })
}

describe('useScraperResults', () => {
  beforeEach(() => {
    // resetAllMocks（而非 clearAllMocks）：清掉上个用例未消费的 mockResolvedValueOnce，
    // 否则「排队的一次性返回值」会漏到下一个用例里，让失败变得随机
    vi.resetAllMocks()
    mocks.get.mockResolvedValue(page([{ id: 'a' }, { id: 'b' }], 2))
    mocks.post.mockResolvedValue({
      data: { trashed_count: 2, skipped: 0, remaining: 0 },
    })
  })

  it('hasMoreResults：已加载数 < 总数时为真', async () => {
    mocks.get.mockResolvedValue(page([{ id: 'a' }], 3))
    const r = setup()
    expect(r.hasMoreResults.value).toBe(false) // 尚未加载

    await r.viewResults(1)

    expect(r.resultsItems.value).toHaveLength(1)
    expect(r.resultsTotal.value).toBe(3)
    expect(r.hasMoreResults.value).toBe(true)
  })

  describe('打开 / 收起', () => {
    it('首次打开：请求第 1 页并替换列表、重置勾选', async () => {
      const r = setup()
      r.selectedIds.value = new Set(['old'])

      await r.viewResults(5)

      expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks/5/results', {
        params: { page: 1, size: 100 },
      })
      expect(r.resultsTaskId.value).toBe(5)
      expect(r.selectedIds.value.size).toBe(0)
      expect(r.resultsItems.value.map((i: { id: string }) => i.id)).toEqual(['a', 'b'])
    })

    it('再次点击同一任务 = 收起，清空所有状态且不再请求', async () => {
      const r = setup()
      await r.viewResults(5)
      mocks.get.mockClear()

      await r.viewResults(5)

      expect(mocks.get).not.toHaveBeenCalled()
      expect(r.resultsTaskId.value).toBeNull()
      expect(r.resultsItems.value).toEqual([])
      expect(r.resultsTotal.value).toBe(0)
      expect(r.selectedIds.value.size).toBe(0)
    })

    it('切换任务时旧响应被丢弃（竞态防护）', async () => {
      const r = setup()
      let resolveOld: (v: unknown) => void = () => {}
      mocks.get
        .mockImplementationOnce(() => new Promise((resolve) => (resolveOld = resolve)))
        .mockResolvedValueOnce(page([{ id: 'new-task-item' }], 1))

      const oldCall = r.viewResults(1) // 在途
      await r.viewResults(2) // 先切到任务 2 并返回
      resolveOld(page([{ id: 'stale' }], 99)) // 旧任务响应后到
      await oldCall

      // 列表仍是任务 2 的（旧响应没覆盖）
      expect(r.resultsTaskId.value).toBe(2)
      expect(r.resultsItems.value.map((i: { id: string }) => i.id)).toEqual(['new-task-item'])
      expect(r.resultsTotal.value).toBe(1)
    })
  })

  describe('加载更多', () => {
    it('追加下一页并去重，保留勾选', async () => {
      mocks.get
        .mockResolvedValueOnce(page([{ id: 'a' }, { id: 'b' }], 3))
        .mockResolvedValueOnce(page([{ id: 'b' }, { id: 'c' }], 3)) // b 重复返回
      const r = setup()
      await r.viewResults(1)
      r.selectedIds.value = new Set(['a'])

      await r.loadMoreResults()

      expect(r.resultsItems.value.map((i: { id: string }) => i.id)).toEqual(['a', 'b', 'c'])
      expect(r.selectedIds.value.has('a')).toBe(true)
    })

    it('没有更多 / 加载中 / 未展开时都不请求', async () => {
      const r = setup()
      await r.loadMoreResults() // 未展开
      expect(mocks.get).not.toHaveBeenCalled()

      mocks.get.mockResolvedValueOnce(page([{ id: 'a' }], 1))
      await r.viewResults(1)
      mocks.get.mockClear()
      await r.loadMoreResults() // 已无更多
      expect(mocks.get).not.toHaveBeenCalled()

      mocks.get.mockResolvedValueOnce(page([{ id: 'a' }, { id: 'b' }], 5))
      await r.viewResults(1)
      r.resultsLoading.value = true
      mocks.get.mockClear()
      await r.loadMoreResults() // 加载中
      expect(mocks.get).not.toHaveBeenCalled()
    })

    it('加载失败提示「加载失败」且 loading 复位', async () => {
      mocks.get.mockResolvedValueOnce(page([{ id: 'a' }], 3))
      const r = setup()
      await r.viewResults(1)

      mocks.get.mockRejectedValueOnce(new Error('500'))
      await r.loadMoreResults()

      expect(mocks.message.error).toHaveBeenCalledWith('加载失败')
      expect(r.resultsLoading.value).toBe(false)
    })
  })

  describe('勾选', () => {
    it('toggleSelect 增删（每次新集合）', () => {
      const r = setup()
      r.toggleSelect('a')
      expect([...r.selectedIds.value]).toEqual(['a'])
      r.toggleSelect('b')
      expect([...r.selectedIds.value].sort()).toEqual(['a', 'b'])
      r.toggleSelect('a')
      expect([...r.selectedIds.value]).toEqual(['b'])
    })

    it('selectAll 全选已加载项；再点一次取消', async () => {
      mocks.get.mockResolvedValue(page([{ id: 'a' }, { id: 'b' }], 2))
      const r = setup()
      await r.viewResults(1)

      r.selectAll()
      expect([...r.selectedIds.value].sort()).toEqual(['a', 'b'])

      r.selectAll()
      expect(r.selectedIds.value.size).toBe(0)
    })

    it('只勾了部分时 selectAll 补全为全选', async () => {
      mocks.get.mockResolvedValue(page([{ id: 'a' }, { id: 'b' }], 2))
      const r = setup()
      await r.viewResults(1)
      r.selectedIds.value = new Set(['a'])

      r.selectAll()

      expect([...r.selectedIds.value].sort()).toEqual(['a', 'b'])
    })
  })

  describe('批量移入垃圾桶', () => {
    it('未勾选 / 未展开时不发请求', async () => {
      const r = setup()
      await r.deleteSelected() // 未勾选
      expect(mocks.post).not.toHaveBeenCalled()

      r.resultsTaskId.value = null
      r.selectedIds.value = new Set(['a'])
      await r.deleteSelected() // 未展开
      expect(mocks.post).not.toHaveBeenCalled()
    })

    it('成功：移出本地列表、剩余数更新、刷新任务数、全部删完收起面板', async () => {
      const r = setup()
      await r.viewResults(9)
      r.selectedIds.value = new Set(['a', 'b'])

      await r.deleteSelected()

      expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks/9/results/batch-delete', {
        ids: ['a', 'b'],
      })
      expect(mocks.message.success).toHaveBeenCalledWith('已移入垃圾桶 2 个')
      expect(mocks.refreshTasks).toHaveBeenCalled()
      // remaining=0 → 收起面板
      expect(r.resultsTaskId.value).toBeNull()
      expect(r.resultsItems.value).toEqual([])
      expect(r.deletingResults.value).toBe(false)
    })

    it('有跳过条目时提示里带「已在垃圾桶」', async () => {
      mocks.post.mockResolvedValue({
        data: { trashed_count: 1, skipped: 3, remaining: 5 },
      })
      const r = setup()
      await r.viewResults(9)
      r.selectedIds.value = new Set(['a'])

      await r.deleteSelected()

      expect(mocks.message.success).toHaveBeenCalledWith(
        '已移入垃圾桶 1 个（3 个已在垃圾桶，已跳过）',
      )
      expect(r.resultsTotal.value).toBe(5)
      expect(r.resultsTaskId.value).toBe(9) // 还有剩余：面板保持展开
    })

    it('当前页删空但仍有剩余 → 回到第一页重新加载', async () => {
      mocks.get.mockResolvedValueOnce(page([{ id: 'a' }], 1))
      const r = setup()
      await r.viewResults(9) // 只加载 1 条
      r.selectedIds.value = new Set(['a']) // 全删 → 本地列表空
      mocks.post.mockResolvedValue({ data: { trashed_count: 1, skipped: 0, remaining: 4 } })
      mocks.get.mockResolvedValueOnce(page([{ id: 'next-page-item' }], 4))

      await r.deleteSelected()

      expect(mocks.get).toHaveBeenLastCalledWith('/scraper/tasks/9/results', {
        params: { page: 1, size: 100 },
      })
      expect(r.resultsItems.value.map((i: { id: string }) => i.id)).toEqual(['next-page-item'])
    })

    it('删除失败提示且复位 deleting', async () => {
      mocks.post.mockRejectedValue(new Error('500'))
      const r = setup()
      await r.viewResults(9)
      r.selectedIds.value = new Set(['a'])

      await r.deleteSelected()

      expect(mocks.message.error).toHaveBeenCalledWith('删除失败')
      expect(r.deletingResults.value).toBe(false)
      expect(mocks.refreshTasks).not.toHaveBeenCalled()
    })
  })
})
