/**
 * 采集管理页任务域（`useScraperTasks`）的测试。
 *
 * 这个 composable 是采集管理页的「数据 + 操作」中枢，以前 0% 覆盖。它的分支判据错了
 * 不会报错，但会静默做错事：
 * - **筛选/排序持久化**：写 `localStorage`，刷新后保持；
 * - **cookie 状态探测失败要降级**：单个平台探测挂了不能让整页「加载失败」；
 * - **删除/清空/重试后的进度刷新**：刷新漏了列表就停在旧状态；
 * - **复制任务配置**：config 解析失败/没有关键词要明确提示，不能发一个空任务；
 * - **后端 detail 是对象（Chrome 未启动带启动命令）**：要提示并自动复制命令，
 *   不能只弹「创建失败」让用户干瞪眼；
 * - **轮询节流**：只有存在运行/等待中任务时才每 5 秒刷新。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  copyToClipboard: vi.fn(async () => true),
  polling: {
    start: vi.fn(),
    stop: vi.fn(),
    callback: null as null | (() => Promise<void>),
  },
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.del },
}))

vi.mock('@/utils/clipboard', () => ({ copyToClipboard: mocks.copyToClipboard }))

vi.mock('@/composables/usePolling', () => ({
  usePolling: (opts: { callback: () => Promise<void> }) => {
    mocks.polling.callback = opts.callback
    return { start: mocks.polling.start, stop: mocks.polling.stop }
  },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useScraperTasks } from '../useScraperTasks'

const sourcesResponse = {
  data: {
    sources: [
      { platform: 'xiaohongshu', name: '小红书（自定义名）' },
      { platform: 'douyin', name: '抖音' },
    ],
    tombstone_count: 3,
    default_max_count: 20,
  },
}

function tasksResponse(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      items: [{ id: 1, platform: 'xiaohongshu', status: 'running' }],
      total: 10,
      stats: { completed: 8, failed: 2, running: 1, pending: 0 },
      ...overrides,
    },
  }
}

function cookieResponse(platform: string, valid = true) {
  return {
    data: { platform, exists: true, age_hours: 1, valid, hint: '' },
  }
}

/** 按 URL 分派 GET：sources / tasks / cookie-status（可指定哪些失败） */
function setupGet(
  overrides: { tasks?: unknown; sourcesFails?: boolean; cookieFails?: string[] } = {},
) {
  const cookieFails = overrides.cookieFails ?? []
  mocks.get.mockImplementation((url: string, opts?: { params?: { platform?: string } }) => {
    if (url === '/scraper/sources') {
      return overrides.sourcesFails
        ? Promise.reject(new Error('sources down'))
        : Promise.resolve(sourcesResponse)
    }
    if (url === '/scraper/tasks') return Promise.resolve(overrides.tasks ?? tasksResponse())
    if (url === '/scraper/cookie-status') {
      const platform = opts?.params?.platform ?? ''
      return cookieFails.includes(platform)
        ? Promise.reject(new Error('cookie check failed'))
        : Promise.resolve(cookieResponse(platform))
    }
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
}

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

describe('useScraperTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.copyToClipboard.mockResolvedValue(true)
    setupGet()
  })

  describe('初始状态与持久化', () => {
    it('筛选/排序从 localStorage 恢复，变更后写回', async () => {
      localStorage.setItem('scraper-task-filter', 'failed')
      localStorage.setItem('scraper-task-sort', 'oldest')

      const t = useScraperTasks()
      expect(t.taskFilterStatus.value).toBe('failed')
      expect(t.taskSort.value).toBe('oldest')

      t.taskFilterStatus.value = 'running'
      t.taskSort.value = 'newest'
      await flush()

      expect(localStorage.getItem('scraper-task-filter')).toBe('running')
      expect(localStorage.getItem('scraper-task-sort')).toBe('newest')
    })

    it('无持久化时用默认值', () => {
      const t = useScraperTasks()
      expect(t.taskFilterStatus.value).toBe('')
      expect(t.taskSort.value).toBe('newest')
      expect(t.hasFailedTasks.value).toBe(false)
      expect(t.taskStats.value).toEqual({
        total: 0,
        completed: 0,
        failed: 0,
        running: 0,
        pending: 0,
        rate: 0,
      })
    })
  })

  describe('loadAll', () => {
    it('并行拉取并写入来源、任务、统计、墓碑数与 cookie 状态', async () => {
      const t = useScraperTasks()
      await t.loadAll()

      expect(t.sources.value).toHaveLength(2)
      expect(t.tasks.value).toHaveLength(1)
      expect(t.taskTotal.value).toBe(10)
      expect(t.tombstoneCount.value).toBe(3)
      expect(t.defaultMaxCount.value).toBe(20)
      expect(t.cookieStatuses.value.xiaohongshu.valid).toBe(true)
      expect(t.cookieStatuses.value.douyin.platform).toBe('douyin')
      // 任务请求带上筛选/排序/分页参数
      expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks', {
        params: {
          platform: undefined,
          status: undefined,
          sort: 'newest',
          page: 1,
          size: 50,
        },
      })
    })

    it('统计派生：完成率按总数四舍五入，总数 0 时为 0', async () => {
      const t = useScraperTasks()
      await t.loadAll()
      expect(t.taskStats.value).toEqual({
        total: 10,
        completed: 8,
        failed: 2,
        running: 1,
        pending: 0,
        rate: 80,
      })
      expect(t.taskStats.value.running).toBe(1) // 有活动任务
      expect(t.hasFailedTasks.value).toBe(true)

      setupGet({ tasks: tasksResponse({ total: 0, stats: undefined }) })
      await t.loadAll()
      expect(t.taskStats.value.rate).toBe(0)
      expect(t.hasFailedTasks.value).toBe(false)
    })

    it('cookie 探测失败时降级为「检查失败」，不拖垮整页', async () => {
      setupGet({ cookieFails: ['xiaohongshu'] })
      const t = useScraperTasks()

      await t.loadAll()

      expect(t.cookieStatuses.value.xiaohongshu.exists).toBe(false)
      expect(t.cookieStatuses.value.xiaohongshu.hint).toBe('检查失败')
      expect(t.cookieStatuses.value.douyin.valid).toBe(true) // 另一个平台正常
      expect(mocks.message.error).not.toHaveBeenCalled()
    })

    it('整体请求失败提示「加载失败」', async () => {
      setupGet({ sourcesFails: true })
      const t = useScraperTasks()

      await t.loadAll()

      expect(mocks.message.error).toHaveBeenCalledWith('加载失败')
    })
  })

  describe('refreshTasks 与翻页', () => {
    it('刷新成功更新列表与统计', async () => {
      const t = useScraperTasks()
      mocks.get.mockResolvedValueOnce(tasksResponse({ total: 5, stats: { completed: 5 } }))

      await t.refreshTasks()

      expect(t.taskTotal.value).toBe(5)
      expect(t.taskStats.value.completed).toBe(5)
    })

    it('刷新失败静默保持旧数据（轮询失败不能刷成空列表）', async () => {
      const t = useScraperTasks()
      await t.loadAll()
      const before = t.tasks.value

      mocks.get.mockRejectedValueOnce(new Error('poll failed'))
      await t.refreshTasks()

      expect(t.tasks.value).toBe(before)
      expect(mocks.message.error).not.toHaveBeenCalled()
    })

    it('筛选变化回到第一页，翻页更新页码', async () => {
      const t = useScraperTasks()
      t.taskPage.value = 3

      t.onFilterChange()
      expect(t.taskPage.value).toBe(1)

      t.onPageChange(2)
      expect(t.taskPage.value).toBe(2)
      await flush()
    })
  })

  describe('任务操作', () => {
    it('取消任务成功提示并刷新', async () => {
      mocks.post.mockResolvedValue({ data: {} })
      const t = useScraperTasks()

      await t.cancelTask(9)

      expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks/9/cancel')
      expect(mocks.message.success).toHaveBeenCalledWith('已取消')
    })

    it('取消失败提示后端原因', async () => {
      mocks.post.mockRejectedValue(null) // 无 message → 用兜底文案
      const t = useScraperTasks()

      await t.cancelTask(9)

      expect(mocks.message.error).toHaveBeenCalledWith('取消失败')
    })

    it('删除单条任务：200 成功提示；期间 loading 复位', async () => {
      mocks.del.mockResolvedValue({ status: 200, data: {} })
      const t = useScraperTasks()

      await t.deleteSingleTask(3)

      expect(mocks.del).toHaveBeenCalledWith('/scraper/tasks/3')
      expect(mocks.message.success).toHaveBeenCalledWith('已删除')
      expect(t.deletingTask.value).toBeNull()
    })

    it('删除单条任务：失败提示「删除失败: 原因」', async () => {
      mocks.del.mockRejectedValue({
        response: { status: 500, data: { detail: '任务不存在' } },
      })
      const t = useScraperTasks()

      await t.deleteSingleTask(3)

      expect(mocks.message.error).toHaveBeenCalledWith('删除失败: 任务不存在')
      expect(t.deletingTask.value).toBeNull()
    })

    it('清空全部任务：成功后回第一页并复位 clearing', async () => {
      mocks.del.mockResolvedValue({ status: 200, data: {} })
      const t = useScraperTasks()
      t.taskPage.value = 4

      await t.clearAllTasks()

      expect(t.taskPage.value).toBe(1)
      expect(mocks.message.success).toHaveBeenCalledWith('已清空')
      expect(t.clearing.value).toBe(false)
    })

    it('清空失败提示且复位 clearing', async () => {
      mocks.del.mockRejectedValue(new Error('500'))
      const t = useScraperTasks()

      await t.clearAllTasks()

      expect(mocks.message.error).toHaveBeenCalledWith('清空失败')
      expect(t.clearing.value).toBe(false)
    })

    it('批量重试失败任务：成功提示后端文案并启动轮询', async () => {
      mocks.post.mockResolvedValue({ data: { message: '已重新入队 3 个' } })
      const t = useScraperTasks()

      await t.retryFailedTasks()

      expect(mocks.message.success).toHaveBeenCalledWith('已重新入队 3 个')
      expect(mocks.polling.start).toHaveBeenCalled()
      expect(t.retrying.value).toBe(false)
    })

    it('没有失败任务时（404）用 info 提示而不是红色错误', async () => {
      mocks.post.mockRejectedValue({ response: { status: 404, data: { detail: '无失败任务' } } })
      const t = useScraperTasks()

      await t.retryFailedTasks()

      expect(mocks.message.info).toHaveBeenCalledWith('没有失败任务')
      expect(mocks.message.error).not.toHaveBeenCalled()
      expect(t.retrying.value).toBe(false)
    })

    it('单条续采：成功提示断点续采并启动轮询，失败复位 loading', async () => {
      mocks.post.mockResolvedValue({ data: {} })
      const t = useScraperTasks()

      await t.retrySingleTask(5)
      expect(mocks.message.success).toHaveBeenCalledWith('已重新加入队列（断点续采）')
      expect(mocks.polling.start).toHaveBeenCalled()
      expect(t.retryingTask.value).toBeNull()

      mocks.post.mockRejectedValue(null)
      await t.retrySingleTask(6)
      expect(mocks.message.error).toHaveBeenCalledWith('续采失败')
      expect(t.retryingTask.value).toBeNull()
    })
  })

  describe('复制任务配置', () => {
    const task = (config: string | null, platform = 'xiaohongshu') =>
      ({ id: 7, platform, config }) as never

    it('config 解析失败或没有关键词时提示且不发请求', async () => {
      const t = useScraperTasks()

      await t.copyTask(task('不是 JSON'))
      expect(mocks.message.warning).toHaveBeenCalledWith('原任务没有关键词配置，无法复制')

      await t.copyTask(task(JSON.stringify({ keywords: [] })))
      expect(mocks.post).not.toHaveBeenCalled()
    })

    it('按原配置创建任务：小红书带 sort_mode 与 cdp_port', async () => {
      mocks.post.mockResolvedValue({ data: {} })
      const t = useScraperTasks()
      const config = JSON.stringify({
        keywords: ['法式', '通勤'],
        max_count: 50,
        headless: true,
        cdp_port: 9333,
        sort_mode: 'popular',
      })

      await t.copyTask(task(config))

      expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks', {
        platform: 'xiaohongshu',
        keywords: ['法式', '通勤'],
        max_count: 50,
        headless: true,
        cdp_port: 9333,
        sort_mode: 'popular',
      })
      expect(mocks.message.success).toHaveBeenCalledWith('已按原配置创建新采集任务')
      expect(mocks.polling.start).toHaveBeenCalled()
      expect(t.copyingTask.value).toBeNull()
    })

    it('抖音任务不带 sort_mode，cdp_port 为 null', async () => {
      mocks.post.mockResolvedValue({ data: {} })
      const t = useScraperTasks()
      const config = JSON.stringify({ keywords: ['穿搭'], sort_mode: 'popular' })

      await t.copyTask(task(config, 'douyin'))

      expect(mocks.post).toHaveBeenCalledWith('/scraper/tasks', {
        platform: 'douyin',
        keywords: ['穿搭'],
        max_count: 20, // 默认值
        headless: false,
        cdp_port: null,
      })
    })

    it('后端返回带启动命令的对象时提示并延时复制命令', async () => {
      const timeoutSpy = vi.spyOn(globalThis, 'setTimeout')
      mocks.copyToClipboard.mockResolvedValue(true)
      mocks.post.mockRejectedValue({
        response: {
          data: {
            detail: { error: 'Chrome 未启动', command: 'chrome.exe --remote-debugging-port=9222' },
          },
        },
      })
      const t = useScraperTasks()

      await t.copyTask(task(JSON.stringify({ keywords: ['a'] })))

      expect(mocks.message.error).toHaveBeenCalledWith('Chrome 未启动')
      expect(timeoutSpy).toHaveBeenCalledWith(expect.any(Function), 500)
      // 触发延时回调 → 走 copyText 提示「已复制」
      const delayed = timeoutSpy.mock.calls.at(-1)![0] as () => Promise<void>
      await delayed()
      expect(mocks.copyToClipboard).toHaveBeenCalledWith('chrome.exe --remote-debugging-port=9222')
      expect(mocks.message.success).toHaveBeenCalledWith('已复制')
      timeoutSpy.mockRestore()
    })

    it('普通失败用兜底文案', async () => {
      mocks.post.mockRejectedValue(null)
      const t = useScraperTasks()

      await t.copyTask(task(JSON.stringify({ keywords: ['a'] })))

      expect(mocks.message.error).toHaveBeenCalledWith('创建失败')
      expect(t.copyingTask.value).toBeNull()
    })
  })

  describe('展示工具', () => {
    it('copyText 成功/失败分别提示', async () => {
      const t = useScraperTasks()
      await t.copyText('abc')
      expect(mocks.message.success).toHaveBeenCalledWith('已复制')

      mocks.copyToClipboard.mockResolvedValue(false)
      await t.copyText('abc')
      expect(mocks.message.error).toHaveBeenCalledWith('复制失败')
    })

    it('platformName 优先用接口返回名，其次映射表，最后原始值', async () => {
      const t = useScraperTasks()
      await t.loadAll()

      expect(t.platformName('xiaohongshu')).toBe('小红书（自定义名）')
      expect(t.platformName('douyin')).toBe('抖音')
      expect(t.platformName('unknown-platform')).toBe('unknown-platform')
    })

    it('parseKeywords 逗号拼接，空则占位符', () => {
      const t = useScraperTasks()
      expect(t.parseKeywords(JSON.stringify({ keywords: ['法式', '通勤'] }))).toBe('法式, 通勤')
      expect(t.parseKeywords(JSON.stringify({ keywords: [] }))).toBe('-')
      expect(t.parseKeywords(null)).toBe('-')
    })

    it('getTaskDuration 覆盖毫秒/秒/分钟/未开始', () => {
      const t = useScraperTasks()
      const at = (ms: number) => new Date(1_700_000_000_000 + ms).toISOString()

      expect(
        t.getTaskDuration({
          started_at: null,
          finished_at: null,
        } as never),
      ).toBe('-')
      expect(t.getTaskDuration({ started_at: at(0), finished_at: at(500) } as never)).toBe('500ms')
      expect(t.getTaskDuration({ started_at: at(0), finished_at: at(5_000) } as never)).toBe('5s')
      expect(t.getTaskDuration({ started_at: at(0), finished_at: at(90_000) } as never)).toBe(
        '1.5min',
      )
    })
  })

  describe('轮询', () => {
    it('有活动任务时刷新，任务全部结束后自动停止轮询', async () => {
      const t = useScraperTasks()
      await t.loadAll() // running=1 → 内部 hasActiveTasks 为 true
      mocks.get.mockClear()

      // 轮询回调执行时任务已全部完成
      setupGet({ tasks: tasksResponse({ stats: { completed: 10 } }) })
      await mocks.polling.callback!()

      expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks', expect.anything())
      expect(t.taskStats.value.running).toBe(0)
      expect(mocks.polling.stop).toHaveBeenCalled()
    })

    it('无活动任务时不发请求', async () => {
      useScraperTasks()
      mocks.get.mockClear()

      await mocks.polling.callback!()

      expect(mocks.get).not.toHaveBeenCalled()
    })
  })
})
