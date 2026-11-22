/**
 * 采集任务日志查看器（`useScraperLog`）的测试。
 *
 * 日志面板的核心是「快速切换任务时不能串台」：
 * - 再次点击同一任务 = 收起（不能重复请求）；
 * - 旧任务的响应晚到不能覆盖当前任务日志（展开跨度的竞态）；
 * - 加载失败要提示并收起，不能留一个转圈的空面板。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useScraperLog } from '../useScraperLog'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve()
}

describe('useScraperLog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('打开日志：立即记录任务 id、进入 loading，成功后回填内容', async () => {
    const pending = deferred<{ data: { content: string } }>()
    mocks.get.mockReturnValue(pending.promise)
    const log = useScraperLog()

    const promise = log.viewLog(7)

    expect(log.logTaskId.value).toBe(7)
    expect(log.logLoading.value).toBe(true)

    pending.resolve({ data: { content: '采集开始\n完成 3 条' } })
    await promise

    expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks/7/log')
    expect(log.logContent.value).toBe('采集开始\n完成 3 条')
    expect(log.logLoading.value).toBe(false)
  })

  it('再次点击同一任务 = 收起，且不发第二次请求', async () => {
    mocks.get.mockResolvedValue({ data: { content: '日志' } })
    const log = useScraperLog()
    await log.viewLog(7)

    await log.viewLog(7)

    expect(log.logTaskId.value).toBeNull()
    expect(log.logContent.value).toBe('')
    expect(mocks.get).toHaveBeenCalledTimes(1)
  })

  it('加载失败：提示并收起日志面板', async () => {
    mocks.get.mockRejectedValue(new Error('500'))
    const log = useScraperLog()

    await log.viewLog(7)

    expect(mocks.message.error).toHaveBeenCalledWith('日志加载失败')
    expect(log.logTaskId.value).toBeNull()
    expect(log.logContent.value).toBe('')
    expect(log.logLoading.value).toBe(false)
  })

  it('竞态：快速切到任务 2 后，任务 1 的日志响应不能覆盖', async () => {
    const slow = deferred<{ data: { content: string } }>()
    const fast = deferred<{ data: { content: string } }>()
    mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const log = useScraperLog()

    const first = log.viewLog(1)
    const second = log.viewLog(2)

    fast.resolve({ data: { content: '任务 2 的日志' } })
    await second
    expect(log.logContent.value).toBe('任务 2 的日志')

    slow.resolve({ data: { content: '任务 1 的日志' } })
    await first

    expect(log.logContent.value).toBe('任务 2 的日志')
    expect(log.logTaskId.value).toBe(2)
    expect(log.logLoading.value).toBe(false)
  })

  it('竞态：旧任务请求失败静默丢弃，不提示也不影响当前任务', async () => {
    const slow = deferred<{ data: { content: string } }>()
    const fast = deferred<{ data: { content: string } }>()
    mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    const log = useScraperLog()

    const first = log.viewLog(1)
    const second = log.viewLog(2)

    fast.resolve({ data: { content: '任务 2 的日志' } })
    await second

    slow.reject(new Error('500'))
    await first
    await flush()

    // 旧任务的失败属于已过期的请求：不弹错误（否则快速切换会多弹一次），也不再动当前状态
    expect(mocks.message.error).not.toHaveBeenCalled()
    expect(log.logTaskId.value).toBe(2)
    expect(log.logContent.value).toBe('任务 2 的日志')
    expect(log.logLoading.value).toBe(false)
  })

  it('closeLog：清空 id 与内容', async () => {
    mocks.get.mockResolvedValue({ data: { content: '日志' } })
    const log = useScraperLog()
    await log.viewLog(3)

    log.closeLog()

    expect(log.logTaskId.value).toBeNull()
    expect(log.logContent.value).toBe('')
  })
})
