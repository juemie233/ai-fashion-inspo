/**
 * 博主主页信息补全（`useBloggerEnrich`）的测试。
 *
 * 这条链路会**批量发起补全任务并轮询**，还把「确定性拿不到信息」的博主持久化进跳过表
 * （以后不再反复尝试）。原覆盖率 0%，出错的表现是：
 * - 勾选为空也发任务（白跑一次批量采集）；
 * - 轮询没等终态就关弹窗（用户看不到失败明细，也无法重试）；
 * - 跳过/解除跳过没刷新父级计数（缺失数永远不变，用户以为没生效）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  enrichMissingProfiles: vi.fn(),
  fetchEnrichSkips: vi.fn(),
  fetchMissingProfiles: vi.fn(),
  skipEnrichBloggers: vi.fn(),
  unskipEnrichBloggers: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  onFinished: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get } }))

vi.mock('@/api/persons', () => ({
  enrichMissingProfiles: mocks.enrichMissingProfiles,
  fetchEnrichSkips: mocks.fetchEnrichSkips,
  fetchMissingProfiles: mocks.fetchMissingProfiles,
  skipEnrichBloggers: mocks.skipEnrichBloggers,
  unskipEnrichBloggers: mocks.unskipEnrichBloggers,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useBloggerEnrich } from '../useBloggerEnrich'

function missing(id: number, name = `博主${id}`) {
  return { id, name, platform: 'douyin' }
}

function taskState(status: string, result: Record<string, unknown> | null = null) {
  return { id: 7, status, progress: 50, done: 1, total: 2, error: null, result }
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function setup() {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const api = useBloggerEnrich({ onFinished: mocks.onFinished })
  warn.mockRestore()
  return api
}

describe('useBloggerEnrich', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mocks.fetchMissingProfiles.mockResolvedValue({ items: [missing(1), missing(2)], total: 2 })
    mocks.enrichMissingProfiles.mockResolvedValue({ task_id: 7, message: '已提交 2 位' })
    mocks.get.mockResolvedValue({ data: taskState('success') })
    mocks.fetchEnrichSkips.mockResolvedValue({ items: [{ blogger_id: 1, reason: '无主页' }] })
    mocks.skipEnrichBloggers.mockResolvedValue({ skipped: 2 })
    mocks.unskipEnrichBloggers.mockResolvedValue({ unskipped: 1 })
  })

  /** 让轮询循环跑完：先等一次 get，再推进 2s 定时器，直到终态 */
  async function runPoll(promise: Promise<void>) {
    await flush()
    await vi.advanceTimersByTimeAsync(2000)
    await promise
  }

  describe('打开弹窗与勾选', () => {
    it('拉取缺失博主并默认全选', async () => {
      const api = setup()

      await api.openEnrichModal()

      expect(api.missingItems).toHaveLength(2)
      expect([...api.selectedMissingIds]).toEqual([1, 2])
      expect(api.enrichTask).toBeNull()
    })

    it('加载失败提示，不残留旧任务', async () => {
      mocks.fetchMissingProfiles.mockRejectedValue(null)
      const api = setup()
      api.enrichTask = taskState('running') as never

      await api.openEnrichModal()

      expect(mocks.message.error).toHaveBeenCalledWith('加载缺失博主失败')
      expect(api.enrichTask).toBeNull()
    })

    it('toggleMissing 增删选中项（不改原集合）', () => {
      const api = setup()
      api.selectedMissingIds = new Set([1])
      const before = api.selectedMissingIds

      api.toggleMissing(2, true)
      expect([...api.selectedMissingIds]).toEqual([1, 2])
      api.toggleMissing(1, false)
      expect([...api.selectedMissingIds]).toEqual([2])
      expect(before).not.toBe(api.selectedMissingIds) // 每次新集合，触发响应式
    })
  })

  describe('开始补全', () => {
    it('未勾选时警告且不发任务', async () => {
      mocks.fetchMissingProfiles.mockResolvedValue({ items: [] })
      const api = setup()
      await api.openEnrichModal()

      await api.startEnrich()

      expect(mocks.message.warning).toHaveBeenCalledWith('请至少勾选一位博主')
      expect(mocks.enrichMissingProfiles).not.toHaveBeenCalled()
    })

    it('提交选中博主并轮询到终态，busy 复位', async () => {
      const api = setup()
      await api.openEnrichModal()

      const promise = api.startEnrich()
      expect(api.enrichBusy).toBe(true)
      await runPoll(promise)

      expect(mocks.enrichMissingProfiles).toHaveBeenCalledWith([1, 2])
      expect(mocks.message.success).toHaveBeenCalledWith('已提交 2 位')
      expect(api.enrichTask?.status).toBe('success')
      expect(api.enrichBusy).toBe(false)
    })

    it('轮询会等待 pending/running，直到终态才结束', async () => {
      mocks.get
        .mockResolvedValueOnce({ data: taskState('pending') })
        .mockResolvedValueOnce({ data: taskState('running') })
        .mockResolvedValueOnce({ data: taskState('failed', null) })
      const api = setup()
      await api.openEnrichModal()

      const promise = api.startEnrich()
      await flush()
      await vi.advanceTimersByTimeAsync(2000) // pending → running
      await vi.advanceTimersByTimeAsync(2000) // running → failed
      await promise

      expect(mocks.get).toHaveBeenCalledTimes(3)
      expect(api.enrichTask?.status).toBe('failed')
    })

    it('提交失败提示且 busy 复位', async () => {
      mocks.enrichMissingProfiles.mockRejectedValue(null)
      const api = setup()
      await api.openEnrichModal()

      await api.startEnrich()

      expect(mocks.message.error).toHaveBeenCalledWith('创建补全任务失败')
      expect(api.enrichBusy).toBe(false)
    })
  })

  describe('失败重试', () => {
    it('没有失败项 → info 提示，不发请求', async () => {
      const api = setup()
      api.enrichTask = taskState('success', {
        results: [{ blogger_id: 1, status: 'updated' }],
      }) as never

      await api.retryFailed()

      expect(mocks.message.info).toHaveBeenCalledWith('没有失败的博主')
      expect(mocks.enrichMissingProfiles).not.toHaveBeenCalled()
    })

    it('只用失败项的 blogger_id 重试', async () => {
      const api = setup()
      api.enrichTask = taskState('partial', {
        results: [
          { blogger_id: 3, status: 'failed' },
          { blogger_id: 4, status: 'updated' },
          { blogger_id: 5, status: 'failed' },
        ],
      }) as never

      const promise = api.retryFailed()
      await runPoll(promise)

      expect(mocks.enrichMissingProfiles).toHaveBeenCalledWith([3, 5])
      expect(api.enrichBusy).toBe(false)
    })

    it('重试提交失败提示', async () => {
      mocks.enrichMissingProfiles.mockRejectedValue(null)
      const api = setup()
      api.enrichTask = taskState('partial', {
        results: [{ blogger_id: 3, status: 'failed' }],
      }) as never

      await api.retryFailed()

      expect(mocks.message.error).toHaveBeenCalledWith('创建重试任务失败')
    })
  })

  describe('结果统计', () => {
    it('从 task.result 派生 updated/skipped/failed/douyin_updated 与明细分类', () => {
      const api = setup()
      api.enrichTask = taskState('success', {
        updated: 5,
        skipped: 2,
        failed: 1,
        douyin_updated: 3,
        results: [
          { blogger_id: 1, name: '甲', status: 'updated' },
          { blogger_id: 2, name: '乙', status: 'skipped', reason: '无主页' },
          { blogger_id: 3, name: '丙', status: 'failed', reason: '超时' },
        ],
      }) as never

      expect(api.enrichUpdated).toBe(5)
      expect(api.enrichSkipped).toBe(2)
      expect(api.enrichFailed).toBe(1)
      expect(api.enrichDouyinUpdated).toBe(3)
      expect(api.enrichFailedItems.map((r) => r.name)).toEqual(['丙'])
      expect(api.enrichSkippedItems.map((r) => r.name)).toEqual(['乙'])
    })

    it('任务为空时统计全是 0，明细为空数组', () => {
      const api = setup()
      expect(api.enrichUpdated).toBe(0)
      expect(api.enrichSkipped).toBe(0)
      expect(api.enrichFailed).toBe(0)
      expect(api.enrichDouyinUpdated).toBe(0)
      expect(api.enrichFailedItems).toEqual([])
      expect(api.enrichSkippedItems).toEqual([])
    })
  })

  describe('关闭与跳过管理', () => {
    it('closeEnrich 清空任务并回调父级', () => {
      const api = setup()
      api.enrichTask = taskState('success') as never

      api.closeEnrich()

      expect(api.enrichTask).toBeNull()
      expect(mocks.onFinished).toHaveBeenCalled()
    })

    it('loadSkipList 成功写入，失败静默', async () => {
      const api = setup()
      await api.loadSkipList()
      expect(api.skippedItems).toEqual([{ blogger_id: 1, reason: '无主页' }])

      mocks.fetchEnrichSkips.mockRejectedValue(new Error('500'))
      await api.loadSkipList() // 不提示
      expect(mocks.message.error).not.toHaveBeenCalled()
    })

    it('跳过错博主：空数组不发请求；成功刷新列表并回调父级', async () => {
      const api = setup()
      await api.skipFailedBloggers([])
      expect(mocks.skipEnrichBloggers).not.toHaveBeenCalled()

      await api.skipFailedBloggers([2, 3])
      expect(mocks.skipEnrichBloggers).toHaveBeenCalledWith([2, 3], '手动跳过（无法获取信息）')
      expect(mocks.message.success).toHaveBeenCalledWith('已跳过 2 位博主（解除后可重新纳入）')
      expect(mocks.fetchEnrichSkips).toHaveBeenCalled()
      expect(mocks.onFinished).toHaveBeenCalled()
      expect(api.skipBusy).toBe(false)
    })

    it('跳过失败提示并复位 busy', async () => {
      mocks.skipEnrichBloggers.mockRejectedValue(null)
      const api = setup()

      await api.skipFailedBloggers([1])

      expect(mocks.message.error).toHaveBeenCalledWith('跳过失败')
      expect(api.skipBusy).toBe(false)
    })

    it('解除跳过：成功/失败与空数组', async () => {
      const api = setup()
      await api.unskipBloggers([])
      expect(mocks.unskipEnrichBloggers).not.toHaveBeenCalled()

      await api.unskipBloggers([1])
      expect(mocks.message.success).toHaveBeenCalledWith('已解除 1 位博主（重新纳入补全范围）')
      expect(mocks.onFinished).toHaveBeenCalled()

      mocks.unskipEnrichBloggers.mockRejectedValue(null)
      await api.unskipBloggers([2])
      expect(mocks.message.error).toHaveBeenCalledWith('解除失败')
      expect(api.skipBusy).toBe(false)
    })
  })
})
