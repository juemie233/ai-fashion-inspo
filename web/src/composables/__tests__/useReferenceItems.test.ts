/**
 * 参考图缩略图数据：防抖 + 丢弃过期响应。
 *
 * 为什么这两条必须有测试：
 * - 没有防抖 → 手输 id 时**每个字符一个请求**（`/inspirations?ids=` 会瞬间刷屏）；
 * - 没有序号守卫 → 选择器一次确认 10 张、或快速改选时，迟到的旧响应会盖掉新结果，
 *   缩略图与实际参考顺序对不上（第 1 张是主参考，错位会直接误导出图）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useReferenceItems } from '../useReferenceItems'

function item(id: string) {
  return { id, file_path: `images/${id}.jpg`, thumbnail_path: `thumbnails/${id}.jpg` }
}

describe('useReferenceItems', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('连续改选合并成一次请求（取最后一次选择）', async () => {
    const fetcher = vi.fn().mockResolvedValue([item('a'), item('b')])
    const { items, set } = useReferenceItems(fetcher)

    // 模拟手输：每敲一个字符抛一次变化
    set(['a'])
    set(['a', 'b'])
    set(['a', 'b', 'c'])
    expect(fetcher).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(300)

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher).toHaveBeenCalledWith(['a', 'b', 'c'])
    expect(items.value.map((i) => i.id)).toEqual(['a', 'b'])
  })

  it('迟到的旧响应不覆盖新结果（序号守卫）', async () => {
    const pending: Array<(v: unknown) => void> = []
    // 显式标注返回类型：否则 mock 推断成 Promise<unknown>，传进 useReferenceItems 类型不匹配
    const fetcher = vi.fn(
      () =>
        new Promise<unknown>((resolve) => {
          pending.push(resolve)
        }),
    ) as unknown as (ids: string[]) => Promise<never[]>
    const { items, set } = useReferenceItems(fetcher)

    set(['old'])
    await vi.advanceTimersByTimeAsync(300) // 第 1 个请求发出
    set(['new'])
    await vi.advanceTimersByTimeAsync(300) // 第 2 个请求发出

    // 第 2 个（新）先回，第 1 个（旧）后回：旧结果必须被丢弃
    pending[1]([item('new')])
    await vi.advanceTimersByTimeAsync(0)
    expect(items.value.map((i) => i.id)).toEqual(['new'])

    pending[0]([item('old')])
    await vi.advanceTimersByTimeAsync(0)
    expect(items.value.map((i) => i.id)).toEqual(['new'])
  })

  it('清空选择：立刻清空缩略图且不发请求', async () => {
    const fetcher = vi.fn().mockResolvedValue([item('a')])
    const { items, set } = useReferenceItems(fetcher)

    set(['a'])
    await vi.advanceTimersByTimeAsync(300)
    expect(items.value).toHaveLength(1)

    set([])
    expect(items.value).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(300)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('相同选择重复抛不重复请求（面板挂载时会先抛一次空数组）', async () => {
    const fetcher = vi.fn().mockResolvedValue([item('a')])
    const { set } = useReferenceItems(fetcher)

    set([])
    set([])
    set(['a'])
    set(['a'])
    await vi.advanceTimersByTimeAsync(300)

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher).toHaveBeenCalledWith(['a'])
  })

  it('请求失败：清空且不抛错（缩略图只是辅助）', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('boom'))
    const { items, loading, set } = useReferenceItems(fetcher)

    set(['a'])
    await vi.advanceTimersByTimeAsync(300)

    expect(items.value).toHaveLength(0)
    expect(loading.value).toBe(false)
  })

  it('dispose 取消待发请求', async () => {
    const fetcher = vi.fn().mockResolvedValue([item('a')])
    const { set, dispose } = useReferenceItems(fetcher)

    set(['a'])
    dispose()
    await vi.advanceTimersByTimeAsync(300)

    expect(fetcher).not.toHaveBeenCalled()
  })
})
