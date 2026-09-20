/**
 * 任务轮询骨架（pollTaskUntilIdle）测试。
 *
 * 为什么值得单测：它是人脸页扫描/匹配（3s）与聚合聚类（2s）两处轮询的唯一实现，
 * 收尾条件写错会让「任务跑完了界面还一直转」或「任务还在跑就提前收尾」——
 * 两种都只在真机上偶发，回归成本高。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { pollTaskUntilIdle } from '../taskPollUntilIdle'

/** 依次返回给定快照（用完停在最后一个） */
function sequenceOf(states: ({ id: number; status: string } | null)[]) {
  let i = 0
  return vi.fn(async () => {
    const value = states[Math.min(i, states.length - 1)]
    i += 1
    return value
  })
}

describe('pollTaskUntilIdle', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('任务离开 running/pending 后收尾一次', async () => {
    vi.useFakeTimers()
    const refresh = sequenceOf([
      { id: 7, status: 'running' },
      { id: 7, status: 'pending' },
      { id: 7, status: 'success' },
    ])
    const onIdle = vi.fn()

    const done = pollTaskUntilIdle({ taskId: 7, intervalMs: 1000, refresh, onIdle })
    await vi.advanceTimersByTimeAsync(3000)
    await done

    expect(refresh).toHaveBeenCalledTimes(3)
    expect(onIdle).toHaveBeenCalledTimes(1)
  })

  it('轮询期间任务被换成别的任务：立即收尾，不再等它跑完', async () => {
    vi.useFakeTimers()
    const refresh = sequenceOf([
      { id: 7, status: 'running' },
      { id: 9, status: 'running' },
    ])
    const onIdle = vi.fn()

    const done = pollTaskUntilIdle({ taskId: 7, intervalMs: 2000, refresh, onIdle })
    await vi.advanceTimersByTimeAsync(4000)
    await done

    expect(refresh).toHaveBeenCalledTimes(2)
    expect(onIdle).toHaveBeenCalledTimes(1)
  })

  it('取不到任务快照（null）也算终态，收尾一次', async () => {
    vi.useFakeTimers()
    const refresh = sequenceOf([null])
    const onIdle = vi.fn()

    const done = pollTaskUntilIdle({ taskId: 7, intervalMs: 1000, refresh, onIdle })
    await vi.advanceTimersByTimeAsync(1000)
    await done

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onIdle).toHaveBeenCalledTimes(1)
  })

  it('首次刷新发生在间隔之后（不会立刻打一次请求）', async () => {
    vi.useFakeTimers()
    const refresh = sequenceOf([{ id: 7, status: 'success' }])

    const done = pollTaskUntilIdle({
      taskId: 7,
      intervalMs: 3000,
      refresh,
      onIdle: vi.fn(),
    })
    await vi.advanceTimersByTimeAsync(2999)
    expect(refresh).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    await done
    expect(refresh).toHaveBeenCalledTimes(1)
  })
})
