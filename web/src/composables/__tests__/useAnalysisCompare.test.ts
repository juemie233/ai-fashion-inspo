/**
 * AI 分析结果对比（`useAnalysisCompare`）的测试。
 *
 * 对比弹窗读的是同一素材的多条分析记录，两个易错点：
 * - **陈旧响应**：用户快速点开 A 再点开 B，A 的响应晚到不能把 B 的数据覆盖掉
 *   （否则界面标题和内容是两个人的分析）；
 * - **批量对比前置校验**：少于 2 条记录没有可对比对象，必须在发请求前拦下。
 * 另外失败时要关掉弹窗并透出后端 detail，不能留一个空弹窗。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get, post: mocks.post } }))

import { useAnalysisCompare } from '../useAnalysisCompare'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('useAnalysisCompare', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  describe('单素材对比 viewCompare', () => {
    it('打开即清空旧数据并显示 loading，成功后回填对比数据', async () => {
      mocks.get.mockResolvedValue({ data: { inspiration_id: 'i1', analyses_count: 2 } })
      const cmp = useAnalysisCompare()
      cmp.compareData.value = { inspiration_id: 'old' } as never

      const promise = cmp.viewCompare('i1')

      // 同步阶段就已经打开弹窗、进入 loading、清掉上一条素材的数据
      expect(cmp.compareVisible.value).toBe(true)
      expect(cmp.compareLoading.value).toBe(true)
      expect(cmp.compareData.value).toBeNull()

      await promise

      expect(mocks.get).toHaveBeenCalledWith('/ai/compare/i1')
      expect(cmp.compareData.value).toEqual({ inspiration_id: 'i1', analyses_count: 2 })
      expect(cmp.compareLoading.value).toBe(false)
    })

    it('失败时提示后端 detail 并关闭弹窗', async () => {
      mocks.get.mockRejectedValue({ response: { data: { detail: '素材不存在' } } })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      await cmp.viewCompare('i404')

      expect(error).toHaveBeenCalledWith('素材不存在')
      expect(cmp.compareVisible.value).toBe(false)
      expect(cmp.compareData.value).toBeNull()
      expect(cmp.compareLoading.value).toBe(false)
    })

    it('失败且无后端文案时回退到兜底文案', async () => {
      mocks.get.mockRejectedValue('boom')
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      await cmp.viewCompare('i1')

      expect(error).toHaveBeenCalledWith('获取对比数据失败')
    })

    it('竞态：后点开的素材先返回后，先点开的陈旧响应不能覆盖', async () => {
      const slow = deferred<{ data: { inspiration_id: string } }>()
      const fast = deferred<{ data: { inspiration_id: string } }>()
      mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
      const cmp = useAnalysisCompare()

      const first = cmp.viewCompare('old-item')
      const second = cmp.viewCompare('new-item')

      fast.resolve({ data: { inspiration_id: 'new-item' } })
      await second
      expect(cmp.compareData.value).toEqual({ inspiration_id: 'new-item' })
      expect(cmp.compareLoading.value).toBe(false)

      slow.resolve({ data: { inspiration_id: 'old-item' } })
      await first

      // 旧响应被丢弃，且不会把 loading 重新打开
      expect(cmp.compareData.value).toEqual({ inspiration_id: 'new-item' })
      expect(cmp.compareLoading.value).toBe(false)
    })

    it('竞态：旧请求失败时不能把新弹窗关掉或弹错误', async () => {
      const slow = deferred<{ data: { inspiration_id: string } }>()
      const fast = deferred<{ data: { inspiration_id: string } }>()
      mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      const first = cmp.viewCompare('old-item')
      const second = cmp.viewCompare('new-item')

      fast.resolve({ data: { inspiration_id: 'new-item' } })
      await second

      slow.reject({ response: { data: { detail: '旧请求失败' } } })
      await first

      expect(error).not.toHaveBeenCalled()
      expect(cmp.compareVisible.value).toBe(true)
      expect(cmp.compareData.value).toEqual({ inspiration_id: 'new-item' })
      expect(cmp.compareLoading.value).toBe(false)
    })
  })

  describe('批量对比 viewCompareBatch', () => {
    it('少于 2 条记录：警告且不发请求、不打开弹窗', async () => {
      const warning = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      await cmp.viewCompareBatch([])
      await cmp.viewCompareBatch([7])

      expect(warning).toHaveBeenCalledTimes(2)
      expect(warning).toHaveBeenCalledWith('请至少勾选 2 条记录进行对比')
      expect(mocks.post).not.toHaveBeenCalled()
      expect(cmp.compareBatchVisible.value).toBe(false)
    })

    it('成功：POST log_ids 并回填批量对比数据', async () => {
      mocks.post.mockResolvedValue({ data: { inspiration_id: 'i1', analyses_count: 3 } })
      const cmp = useAnalysisCompare()
      cmp.compareBatchData.value = { inspiration_id: 'old' } as never

      const promise = cmp.viewCompareBatch([11, 12, 13])

      expect(cmp.compareBatchVisible.value).toBe(true)
      expect(cmp.compareBatchLoading.value).toBe(true)
      expect(cmp.compareBatchData.value).toBeNull()

      await promise

      expect(mocks.post).toHaveBeenCalledWith('/ai/compare-batch', { log_ids: [11, 12, 13] })
      expect(cmp.compareBatchData.value).toEqual({ inspiration_id: 'i1', analyses_count: 3 })
      expect(cmp.compareBatchLoading.value).toBe(false)
    })

    it('失败：透出后端 detail 并关闭批量对比弹窗', async () => {
      mocks.post.mockRejectedValue({ response: { data: { detail: '记录不属于同一素材' } } })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      await cmp.viewCompareBatch([1, 2])

      expect(error).toHaveBeenCalledWith('记录不属于同一素材')
      expect(cmp.compareBatchVisible.value).toBe(false)
      expect(cmp.compareBatchLoading.value).toBe(false)
    })

    it('失败且无后端文案时回退到「批量对比失败」', async () => {
      mocks.post.mockRejectedValue(new Error(''))
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      await cmp.viewCompareBatch([1, 2])

      expect(error).toHaveBeenCalledWith('批量对比失败')
    })

    it('竞态：旧的批量对比响应（成功或失败）都不能覆盖新结果', async () => {
      const slow = deferred<{ data: { inspiration_id: string } }>()
      const fast = deferred<{ data: { inspiration_id: string } }>()
      mocks.post.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const cmp = useAnalysisCompare()

      const first = cmp.viewCompareBatch([1, 2])
      const second = cmp.viewCompareBatch([3, 4])

      fast.resolve({ data: { inspiration_id: 'new-item' } })
      await second

      slow.reject({ response: { data: { detail: '旧请求失败' } } })
      await first

      expect(error).not.toHaveBeenCalled()
      expect(cmp.compareBatchVisible.value).toBe(true)
      expect(cmp.compareBatchData.value).toEqual({ inspiration_id: 'new-item' })
      expect(cmp.compareBatchLoading.value).toBe(false)
    })
  })
})
