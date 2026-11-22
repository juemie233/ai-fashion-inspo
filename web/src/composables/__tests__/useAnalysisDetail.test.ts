/**
 * AI 分析详情（`useAnalysisDetail`）的测试。
 *
 * 详情弹窗跟随「历史记录里的某条分析日志」打开，两个易错点：
 * - **陈旧响应**：快速点开 A 再点开 B，A 的响应晚到不能把 B 的详情盖掉
 *   （否则标题是 B、内容是 A 的分析结果）；
 * - **失败关弹窗**：拉不到详情必须提示并关掉，不能留一个空弹窗。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

import { useAnalysisDetail } from '../useAnalysisDetail'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('useAnalysisDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  it('打开即进入 loading 并清空上一条详情，成功后回填', async () => {
    mocks.get.mockResolvedValue({ data: { id: 7, model_name: 'qwen3-vl:8b', tags: [] } })
    const detail = useAnalysisDetail()
    detail.currentDetail.value = { id: 1 } as never

    const promise = detail.viewDetail(7)

    expect(detail.detailVisible.value).toBe(true)
    expect(detail.detailLoading.value).toBe(true)
    expect(detail.currentDetail.value).toBeNull()

    await promise

    expect(mocks.get).toHaveBeenCalledWith('/ai/history/7')
    expect(detail.currentDetail.value).toEqual({ id: 7, model_name: 'qwen3-vl:8b', tags: [] })
    expect(detail.detailLoading.value).toBe(false)
  })

  it('失败：透出后端 detail 并关闭弹窗', async () => {
    mocks.get.mockRejectedValue({ response: { data: { detail: '分析记录不存在' } } })
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const detail = useAnalysisDetail()

    await detail.viewDetail(404)

    expect(error).toHaveBeenCalledWith('分析记录不存在')
    expect(detail.detailVisible.value).toBe(false)
    expect(detail.currentDetail.value).toBeNull()
    expect(detail.detailLoading.value).toBe(false)
  })

  it('失败且无后端文案时回退「获取详情失败」', async () => {
    mocks.get.mockRejectedValue('boom')
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const detail = useAnalysisDetail()

    await detail.viewDetail(1)

    expect(error).toHaveBeenCalledWith('获取详情失败')
  })

  it('竞态：后点开的日志先返回后，先点开的陈旧响应不能覆盖', async () => {
    const slow = deferred<{ data: { id: number } }>()
    const fast = deferred<{ data: { id: number } }>()
    mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const detail = useAnalysisDetail()

    const first = detail.viewDetail(1)
    const second = detail.viewDetail(2)

    fast.resolve({ data: { id: 2 } })
    await second
    expect(detail.currentDetail.value).toEqual({ id: 2 })
    expect(detail.detailLoading.value).toBe(false)

    slow.resolve({ data: { id: 1 } })
    await first

    expect(detail.currentDetail.value).toEqual({ id: 2 })
    expect(detail.detailLoading.value).toBe(false)
  })

  it('竞态：旧请求失败不能把新详情弹窗关掉或弹错误', async () => {
    const slow = deferred<{ data: { id: number } }>()
    const fast = deferred<{ data: { id: number } }>()
    mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const detail = useAnalysisDetail()

    const first = detail.viewDetail(1)
    const second = detail.viewDetail(2)

    fast.resolve({ data: { id: 2 } })
    await second

    slow.reject({ response: { data: { detail: '旧请求失败' } } })
    await first

    expect(error).not.toHaveBeenCalled()
    expect(detail.detailVisible.value).toBe(true)
    expect(detail.currentDetail.value).toEqual({ id: 2 })
    expect(detail.detailLoading.value).toBe(false)
  })

  it('失败后重新打开另一条：成功路径不受上一次失败影响', async () => {
    mocks.get.mockRejectedValueOnce(new Error('network'))
    mocks.get.mockResolvedValueOnce({ data: { id: 9 } })
    vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const detail = useAnalysisDetail()

    await detail.viewDetail(1)
    expect(detail.detailVisible.value).toBe(false)

    await detail.viewDetail(9)

    expect(detail.detailVisible.value).toBe(true)
    expect(detail.currentDetail.value).toEqual({ id: 9 })
    expect(detail.detailLoading.value).toBe(false)
  })
})
