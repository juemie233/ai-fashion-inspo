/**
 * usePolling seam 单测：定时器生命周期、幂等 start、卸载自动停止。
 *
 * 使用 fake timers，不等待真实时间；不依赖 Vue 组件挂载。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling } from '../usePolling'

describe('usePolling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('start 幂等：重复调用不重建定时器，回调只触发一次/间隔', () => {
    const cb = vi.fn()
    const { start, stop } = usePolling({ intervalMs: 1000, callback: cb, immediate: false })
    start()
    start()
    start()
    vi.advanceTimersByTime(1000)
    expect(cb).toHaveBeenCalledTimes(1)
    stop()
  })

  it('immediate=true 时 start 立即执行一次回调', () => {
    const cb = vi.fn()
    const { start } = usePolling({ intervalMs: 5000, callback: cb, immediate: true })
    start()
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('stop 后不再触发回调', () => {
    const cb = vi.fn()
    const { start, stop } = usePolling({ intervalMs: 1000, callback: cb, immediate: false })
    start()
    stop()
    vi.advanceTimersByTime(3000)
    expect(cb).not.toHaveBeenCalled()
  })

  it('intervalMs 支持 getter（自适应间隔）', () => {
    const cb = vi.fn()
    const { start, stop } = usePolling({
      intervalMs: () => 1000,
      callback: cb,
      immediate: false,
    })
    start()
    vi.advanceTimersByTime(1000)
    expect(cb).toHaveBeenCalledTimes(1)
    stop()
  })

  it('running 状态随 start/stop 切换', () => {
    const { start, stop, running } = usePolling({
      intervalMs: 1000,
      callback: () => {},
      immediate: false,
    })
    expect(running.value).toBe(false)
    start()
    expect(running.value).toBe(true)
    stop()
    expect(running.value).toBe(false)
  })
})
