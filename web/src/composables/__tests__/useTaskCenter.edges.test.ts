/**
 * 任务中心 composable（useTaskCenter）**补齐用例**：既有 `useTaskCenter.test.ts` 只覆盖
 * 取消/删除/暂停/恢复与活动态判定，本文件补其余编排逻辑：
 *
 * - 加载两个来源（/tasks 与 /scraper/tasks）的参数、失败提示、loading 复位；
 * - 客户端筛选/分页/页码回退（任务数缩减后页码不能停在空页）；
 * - 轮询间隔随 WS 连接状态与活动采集任务变化（采集任务没有 WS 推送，不能降到 30s），
 *   间隔变化后重建定时器；轮询回调「有活动才刷、无活动自停」；
 * - WS 事件：就地更新行、终态/未知任务触发去抖全量刷新、断线重连立即刷新；
 * - retryFailedScraper 的 404「没有失败任务」分支；
 * - 队列任务被物理删除后的本地移除 + 页码校正回调。
 *
 * 轮询与 WS 用替身（不建立真实定时器/连接），只验证 useTaskCenter 自己接线是否正确。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { nextTick } from 'vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  pollStart: vi.fn(),
  pollStop: vi.fn(),
  pollRunning: { value: false },
  pollOptions: null as unknown,
  wsHandlers: [] as Array<{ type: string; handler: (payload: unknown) => void }>,
  wsReconnectHandlers: [] as Array<() => void>,
  wsConnected: false,
  actionOptions: null as unknown,
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.delete },
}))

vi.mock('@/composables/usePolling', () => ({
  usePolling: (options: unknown) => {
    mocks.pollOptions = options
    return { start: mocks.pollStart, stop: mocks.pollStop, running: mocks.pollRunning }
  },
}))

vi.mock('@/composables/useWebSocket', () => ({
  subscribeWs: (type: string, handler: (payload: unknown) => void) => {
    mocks.wsHandlers.push({ type, handler })
    return vi.fn()
  },
  onWsReconnected: (handler: () => void) => {
    mocks.wsReconnectHandlers.push(handler)
    return vi.fn()
  },
  isWsConnected: () => mocks.wsConnected,
}))

vi.mock('@/composables/useTaskActions', () => ({
  useTaskActions: (options: unknown) => {
    mocks.actionOptions = options
    return {
      cancelTask: vi.fn(),
      deleteTask: vi.fn(),
      pauseTask: vi.fn(),
      resumeTask: vi.fn(),
    }
  },
}))

import { useTaskCenter } from '../useTaskCenter'

const msgMock = () => ({ close: () => {} })

/** 后台队列原始行（/tasks 返回项，交给真实 normalizeQueueTask 归一化） */
function queueRaw(id: number, status = 'pending', created_at = '2026-01-01T00:00:00Z') {
  return {
    id,
    type: 'batch_analyze',
    status,
    progress: 0,
    total: 10,
    done: 0,
    result: null,
    error: null,
    created_at,
    updated_at: created_at,
  }
}

/** 采集任务原始行（/scraper/tasks 返回项） */
function scraperRaw(id: number, status = 'pending') {
  return {
    id,
    platform: 'xiaohongshu',
    status,
    config: null,
    items_found: 0,
    items_added: 0,
    error: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** 让 /tasks 与 /scraper/tasks 返回指定列表 */
function mockLists(queue: unknown[] = [], scraper: unknown[] = []) {
  mocks.get.mockImplementation((url: string) => {
    if (url === '/tasks') return Promise.resolve({ data: { items: queue } })
    if (url === '/scraper/tasks') return Promise.resolve({ data: { items: scraper } })
    return Promise.resolve({ data: {} })
  })
}

/** 取用例注入的轮询配置（start 时解析的 intervalMs/callback） */
function pollConfig(): { intervalMs: () => number; callback: () => void } {
  return mocks.pollOptions as { intervalMs: () => number; callback: () => void }
}

/** 当前轮询间隔（intervalMs 是 getter，需要在变更后重新求值） */
function currentPollInterval(): number {
  return pollConfig().intervalMs()
}

/** 统计 /tasks 列表请求次数（loadTasks 会同时打两个来源） */
function tasksListCalls(): number {
  return mocks.get.mock.calls.filter((call) => call[0] === '/tasks').length
}

function taskEvent(over: Record<string, unknown> = {}) {
  return {
    event: 'progress',
    task_id: 5,
    task_type: 'batch_analyze',
    status: 'running',
    progress: 50,
    done: 5,
    total: 10,
    error: null,
    ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.wsHandlers.length = 0
  mocks.wsReconnectHandlers.length = 0
  mocks.wsConnected = false
  mocks.pollRunning.value = false
  mockLists()
  vi.spyOn(Message, 'success').mockImplementation(msgMock)
  vi.spyOn(Message, 'error').mockImplementation(msgMock)
  vi.spyOn(Message, 'info').mockImplementation(msgMock)
  vi.spyOn(Message, 'warning').mockImplementation(msgMock)
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('loadTasks 加载与错误', () => {
  it('同时拉两个来源：/tasks?size=200 与 /scraper/tasks?sort=newest&size=200，合并后按排序口径排列', async () => {
    mockLists([queueRaw(1, 'success', '2026-01-01T00:00:00Z')], [scraperRaw(2, 'pending')])
    const tc = useTaskCenter()

    await tc.loadTasks()

    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 200 } })
    expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks', {
      params: { sort: 'newest', size: 200 },
    })
    expect(tc.tasks.value).toHaveLength(2)
    expect(tc.loading.value).toBe(false)
    // 采集任务归一化为 scraper 类型
    expect(tc.tasks.value.some((t) => t.source === 'scraper' && t.type === 'scraper')).toBe(true)
  })

  it('加载失败：提示「加载任务失败」，loading 复位且不抛错', async () => {
    mocks.get.mockRejectedValue(new Error('network'))
    const tc = useTaskCenter()

    await expect(tc.loadTasks()).resolves.toBeUndefined()

    expect(Message.error).toHaveBeenCalledWith('加载任务失败')
    expect(tc.loading.value).toBe(false)
    expect(tc.tasks.value).toEqual([])
  })

  it('响应缺 items 字段时按空数组处理（不崩）', async () => {
    mocks.get.mockResolvedValue({ data: {} })
    const tc = useTaskCenter()

    await tc.loadTasks()

    expect(tc.tasks.value).toEqual([])
  })
})

describe('筛选与分页', () => {
  it('状态/类型筛选同时生效，total 与 pageCount 随之变化', async () => {
    mockLists([
      queueRaw(1, 'running'),
      queueRaw(2, 'failed'),
      queueRaw(3, 'success'),
      queueRaw(4, 'failed'),
    ])
    const tc = useTaskCenter()
    await tc.loadTasks()

    expect(tc.total.value).toBe(4)
    tc.statusFilter.value = 'failed'
    expect(tc.total.value).toBe(2)
    expect(tc.pageItems.value.map((t) => t.id).sort()).toEqual([2, 4])
    tc.typeFilter.value = 'no_such_type'
    expect(tc.total.value).toBe(0)
    expect(tc.pageCount.value).toBe(1) // 空结果也至少一页
  })

  it('客户端分页：每页 20 条，第 2 页切出对应区间', async () => {
    const queue = Array.from({ length: 45 }, (_, index) =>
      queueRaw(index + 1, 'success', `2026-01-01T00:00:${String(index).padStart(2, '0')}Z`),
    )
    mockLists(queue)
    const tc = useTaskCenter()
    await tc.loadTasks()

    expect(tc.pageCount.value).toBe(3)
    expect(tc.pageItems.value).toHaveLength(20)

    tc.page.value = 2
    expect(tc.pageItems.value).toHaveLength(20)
    tc.page.value = 3
    expect(tc.pageItems.value).toHaveLength(5)
  })

  it('onFilterChange 把页码重置为 1', () => {
    const tc = useTaskCenter()
    tc.page.value = 3

    tc.onFilterChange()

    expect(tc.page.value).toBe(1)
  })

  it('任务数缩减后页码回退到最后一页（避免停在空页）', async () => {
    mockLists(Array.from({ length: 45 }, (_, index) => queueRaw(index + 1, 'success')))
    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(tc.pageCount.value).toBe(3)
    tc.page.value = 3

    mockLists([queueRaw(1, 'success')])
    await tc.loadTasks()

    expect(tc.pageCount.value).toBe(1)
    expect(tc.page.value).toBe(1)
    expect(tc.pageItems.value).toHaveLength(1)
  })
})

describe('活动态判定', () => {
  it('hasFailedScraper 只统计采集任务的 failed，不把队列失败算进去', async () => {
    mockLists([queueRaw(1, 'failed')], [])
    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(tc.hasFailedScraper.value).toBe(false)

    mockLists([queueRaw(1, 'failed')], [scraperRaw(2, 'failed')])
    await tc.loadTasks()
    expect(tc.hasFailedScraper.value).toBe(true)
  })
})

describe('轮询间隔与重启', () => {
  it('WS 已连接且无活动采集任务时用 30s 保底；有活动采集任务降回 5s', async () => {
    mocks.wsConnected = true
    mockLists([], [])
    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(currentPollInterval()).toBe(30000)

    // 队列任务有 WS 推送，不改变间隔
    mockLists([queueRaw(1, 'running')], [])
    await tc.loadTasks()
    expect(currentPollInterval()).toBe(30000)

    // 采集任务没有 WS 推送 → 必须 5s 轮询
    mockLists([queueRaw(1, 'running')], [scraperRaw(2, 'pending')])
    await tc.loadTasks()
    expect(currentPollInterval()).toBe(5000)
  })

  it('WS 未连接时统一 5s（与是否有采集任务无关）', async () => {
    mocks.wsConnected = false
    mockLists([], [])
    const tc = useTaskCenter()
    await tc.loadTasks()

    expect(currentPollInterval()).toBe(5000)
  })

  it('轮询间隔变化且轮询正在跑时：stop + start 重建定时器（间隔只在 start 时解析一次）', async () => {
    mocks.wsConnected = true
    mockLists([], [])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.pollRunning.value = true

    mockLists([], [scraperRaw(2, 'pending')])
    await tc.loadTasks()
    await nextTick()

    expect(mocks.pollStop).toHaveBeenCalled()
    expect(mocks.pollStart).toHaveBeenCalled()
  })

  it('轮询回调：有活动任务时刷新列表，无活动时停止轮询', async () => {
    mockLists([queueRaw(1, 'running')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.get.mockClear()

    pollConfig().callback()
    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 200 } })

    mockLists([])
    await tc.loadTasks()
    mocks.pollStop.mockClear()
    pollConfig().callback()
    expect(mocks.pollStop).toHaveBeenCalled()
  })

  it('空闲自停后重新出现活动任务：watch(hasActive) 自动重启轮询', async () => {
    mockLists([])
    const tc = useTaskCenter()
    await tc.loadTasks()
    expect(mocks.pollStart).not.toHaveBeenCalled()

    mockLists([queueRaw(1, 'running')])
    await tc.loadTasks()
    await nextTick()

    expect(mocks.pollStart).toHaveBeenCalled()
  })
})

describe('retryFailedScraper 重试失败采集任务', () => {
  it('成功：提示后端 message（缺省「已重试」）并刷新列表', async () => {
    mocks.post.mockResolvedValueOnce({ data: { message: '已重试 2 个采集任务' } })
    mockLists()
    const tc = useTaskCenter()

    await tc.retryFailedScraper()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks/retry-failed')
    expect(Message.success).toHaveBeenCalledWith('已重试 2 个采集任务')
    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 200 } })
    expect(tc.retrying.value).toBe(false)
  })

  it('响应无 message 时提示「已重试」', async () => {
    mocks.post.mockResolvedValueOnce({ data: {} })
    const tc = useTaskCenter()

    await tc.retryFailedScraper()

    expect(Message.success).toHaveBeenCalledWith('已重试')
  })

  it('404（没有失败任务）：用 info 提示，不当作错误', async () => {
    mocks.post.mockRejectedValueOnce({ response: { status: 404 } })
    const tc = useTaskCenter()

    await tc.retryFailedScraper()

    expect(Message.info).toHaveBeenCalledWith('没有失败任务')
    expect(Message.error).not.toHaveBeenCalled()
    expect(tc.retrying.value).toBe(false)
  })

  it('其它失败：info 展示后端 detail', async () => {
    mocks.post.mockRejectedValueOnce({
      response: { status: 500, data: { detail: '采集进程未启动' } },
    })
    const tc = useTaskCenter()

    await tc.retryFailedScraper()

    expect(Message.info).toHaveBeenCalledWith('采集进程未启动')
    expect(tc.retrying.value).toBe(false)
  })
})

describe('队列任务删除回调', () => {
  it('本地移除对应队列行并把页码校正到最后一页；reload 回调就是 loadTasks', async () => {
    mockLists(
      Array.from({ length: 21 }, (_, index) => queueRaw(index + 1, 'success')),
      [],
    )
    const tc = useTaskCenter()
    await tc.loadTasks()
    tc.page.value = 2 // 21 条 → 2 页，第 2 页 1 条
    expect(tc.pageCount.value).toBe(2)

    const options = mocks.actionOptions as {
      onQueueTaskDeleted?: (task: { id: number; source: string }) => void
      reload?: () => Promise<void>
    }
    options.onQueueTaskDeleted?.({ id: 21, source: 'queue' })
    await nextTick()

    expect(tc.tasks.value.find((t) => t.id === 21)).toBeUndefined()
    expect(tc.tasks.value).toHaveLength(20)
    expect(tc.pageCount.value).toBe(1)
    expect(tc.page.value).toBe(1)

    // 回调里的 reload 必须接到 loadTasks（删除后全量校正页码）
    mocks.get.mockClear()
    await options.reload?.()
    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 200 } })
  })
})

describe('WebSocket task_event 推送', () => {
  it('订阅类型是 task_event', () => {
    useTaskCenter()
    expect(mocks.wsHandlers[0]?.type).toBe('task_event')
  })

  it('匹配到队列行：就地更新 progress/done/total/status，不触发全量刷新', async () => {
    mockLists([queueRaw(5, 'pending')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.get.mockClear()

    mocks.wsHandlers[0]!.handler(taskEvent())

    const row = tc.tasks.value.find((t) => t.id === 5)
    expect(row?.progress).toBe(50)
    expect(row?.done).toBe(5)
    expect(row?.status).toBe('running')
    expect(mocks.get).not.toHaveBeenCalled()
  })

  it('事件缺字段时只更新提供的字段，不把已有进度重置', async () => {
    mockLists([queueRaw(5, 'running')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    const before = tc.tasks.value.find((t) => t.id === 5)!
    mocks.get.mockClear()
    vi.useFakeTimers()

    // 只带 status 的终态事件：progress/done/total 不出现时保持原值
    mocks.wsHandlers[0]!.handler({
      event: 'success',
      task_id: 5,
      task_type: 'batch_analyze',
      status: 'success',
    })

    const row = tc.tasks.value.find((t) => t.id === 5)!
    expect(row.status).toBe('success')
    expect(row.progress).toBe(before.progress)
    expect(row.done).toBe(before.done)
    expect(row.total).toBe(before.total)

    // 终态触发的去抖刷新也要消费掉，避免残留真实定时器
    await vi.advanceTimersByTimeAsync(300)
  })

  it('failed 事件把后端错误写入行内 error', async () => {
    mockLists([queueRaw(5, 'running')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.get.mockClear()
    vi.useFakeTimers()

    mocks.wsHandlers[0]!.handler(
      taskEvent({ event: 'failed', status: 'failed', error: 'GPU 显存不足' }),
    )

    // 先去抖窗口内断言就地写入的错误（全量刷新会用服务端原始行覆盖本地行）
    const row = tc.tasks.value.find((t) => t.id === 5)
    expect(row?.error).toBe('GPU 显存不足')

    // 终态 → 去抖后全量刷新一次
    await vi.advanceTimersByTimeAsync(300)
    expect(tasksListCalls()).toBe(1)
  })

  it('无效载荷（null / task_id 非数字）直接忽略', async () => {
    mockLists([queueRaw(5, 'pending')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.get.mockClear()
    vi.useFakeTimers()

    mocks.wsHandlers[0]!.handler(null)
    mocks.wsHandlers[0]!.handler({ task_id: 'bad' })
    await vi.advanceTimersByTimeAsync(300)

    expect(mocks.get).not.toHaveBeenCalled()
    expect(tc.tasks.value).toHaveLength(1)
  })

  it('列表里没有的任务：去抖 300ms 后全量刷新（多个事件合并为一次）', async () => {
    mockLists([queueRaw(1, 'running')])
    const tc = useTaskCenter()
    await tc.loadTasks()
    mocks.get.mockClear()
    vi.useFakeTimers()

    mocks.wsHandlers[0]!.handler(taskEvent({ task_id: 99 }))
    mocks.wsHandlers[0]!.handler(taskEvent({ task_id: 98 }))
    await vi.advanceTimersByTimeAsync(299)
    expect(mocks.get).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    // 两个事件只触发一次全量刷新（loadTasks 内部会打两个来源）
    expect(tasksListCalls()).toBe(1)
    expect(tc.tasks.value).toHaveLength(1)
  })

  it('断线重连成功：立即（0ms）全量刷新一次补齐漏掉的事件', async () => {
    mockLists([queueRaw(1, 'running')])
    useTaskCenter()
    await nextTick()
    mocks.get.mockClear()
    vi.useFakeTimers()

    mocks.wsReconnectHandlers[0]!()
    await vi.advanceTimersByTimeAsync(0)

    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { size: 200 } })
  })
})
