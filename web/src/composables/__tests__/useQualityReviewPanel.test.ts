/**
 * 质量审核面板（`useQualityReviewPanel`）的测试。
 *
 * 审核是**写操作且影响负样本口径**（被拒素材进质量初筛器的负样本），所以这里的
 * 每条语义错了都不是「界面不好看」，而是数据被污染或用户白等：
 *
 * 1. **批量提交条数 = min(待审核数, 单次上限)**：按钮文案承诺「审核全部」，写死 200
 *    会让上万条待审核时要反复点几十次；超上限要明确告诉用户「本轮只处理上限条」；
 * 2. **开关失败必须回滚**：`toggleAutoApprove` 先乐观切换，接口失败要恢复原值——
 *    否则界面显示「已关闭」，实际后端还开着（手动上传继续免审核）；
 * 3. **随机审核数量非法时不发请求**（0 / 负数 / 超上限）；
 * 4. **任务轮询**：成功按结果提示「通过/拒绝/未判定/疑似 AI」，失败/取消分别提示；
 *    连续失败 5 次才停（后端重启不该让进度卡死）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  loadRejectedItems: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, put: mocks.put },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useQualityReviewPanel } from '../useQualityReviewPanel'

function stats(overrides: Record<string, unknown> = {}) {
  return {
    total: 100,
    pending: 40,
    approved: 50,
    rejected: 10,
    pass_rate: 0.83,
    ai_generated: 5,
    avg_seconds: 12,
    ...overrides,
  }
}

async function load() {
  return useQualityReviewPanel({ loadRejectedItems: mocks.loadRejectedItems })
}

describe('useQualityReviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    localStorage.clear()
    mocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/tasks/')) {
        // 回显 URL 里的任务 id：轮询首拍会立刻用任务快照覆盖 reviewTask
        const id = Number(url.split('/')[2])
        return Promise.resolve({
          data: { id, status: 'running', progress: 10, total: 40, done: 4 },
        })
      }
      return Promise.resolve({ data: stats() })
    })
    mocks.put.mockResolvedValue({ data: {} })
    mocks.post.mockResolvedValue({ data: { message: 'ok', count: 40, task_id: 7 } })
  })

  it('批量审核条数取 min(待审核, 单次上限)，超上限时给出剩余提示', async () => {
    const { qualityReviewStats, batchSubmitCount, batchOverflowHint } = await load()

    qualityReviewStats.value = stats({ pending: 40 }) as never
    expect(batchSubmitCount.value).toBe(40)
    expect(batchOverflowHint.value).toBe('')

    qualityReviewStats.value = stats({ pending: 6000 }) as never
    // 单次上限 QUALITY_BATCH_MAX = 5000（与后端 le=5000 对齐）
    expect(batchSubmitCount.value).toBe(5000)
    expect(batchOverflowHint.value).toContain('剩余')
    expect(batchOverflowHint.value).toContain('可再点一次')
  })

  it('按钮文案随状态变化：有待审核→审核全部、全部审完→全部已审核', async () => {
    const { qualityReviewStats, batchButtonLabel } = await load()

    qualityReviewStats.value = stats({ pending: 12 }) as never
    expect(batchButtonLabel.value).toContain('审核全部待审核 (12)')

    qualityReviewStats.value = stats({ pending: 0 }) as never
    expect(batchButtonLabel.value).toBe('全部已审核')
  })

  it('触发批量审核：提交上限条数、提示任务号、开始轮询', async () => {
    const { triggerQualityCheck, reviewTask, qualityChecking } = await load()

    const promise = triggerQualityCheck()
    expect(qualityChecking.value).toBe(true)
    await promise

    expect(mocks.post).toHaveBeenCalledWith(
      '/ai/quality-check',
      null,
      expect.objectContaining({ params: expect.objectContaining({ limit: expect.any(Number) }) }),
    )
    expect(mocks.get).toHaveBeenCalledWith('/tasks/7') // 已开始轮询
    expect(reviewTask.value?.id).toBe(7) // 轮询首拍已回填任务快照
    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('任务 #7'))
    expect(qualityChecking.value).toBe(false)
  })

  it('批量审核提交失败：提示且不进入轮询', async () => {
    mocks.post.mockRejectedValue(new Error('Ollama 未启动'))
    const { triggerQualityCheck, reviewTask } = await load()

    await triggerQualityCheck()

    expect(mocks.message.error).toHaveBeenCalled()
    expect(reviewTask.value).toBeNull()
  })

  it('开关「手动上传免审核」失败时回滚（否则界面说关了、后端还开着）', async () => {
    mocks.put.mockRejectedValue(new Error('403'))
    const { autoApproveEnabled, toggleAutoApprove, autoApproveSaving } = await load()

    autoApproveEnabled.value = true
    // Arco switch 走 v-model：点击时先把绑定值翻成 false，再触发 change
    autoApproveEnabled.value = false
    const promise = toggleAutoApprove(false)
    await promise

    expect(autoApproveEnabled.value).toBe(true) // 回滚
    expect(mocks.message.error).toHaveBeenCalled()
    expect(autoApproveSaving.value).toBe(false)
  })

  it('开关成功时保留新值并提示', async () => {
    const { autoApproveEnabled, toggleAutoApprove } = await load()

    autoApproveEnabled.value = false
    autoApproveEnabled.value = true // 模拟 switch 先翻转
    await toggleAutoApprove(true)

    expect(autoApproveEnabled.value).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith(
      '/ai/manual-upload-auto-approve',
      null,
      expect.objectContaining({ params: { enabled: true } }),
    )
    expect(mocks.message.success).toHaveBeenCalledWith('手动上传免审核已开启')
  })

  it('随机审核数量非法时不发请求，只警告', async () => {
    const { randomReviewCount, randomQualityCheck } = await load()

    randomReviewCount.value = 0
    await randomQualityCheck()
    expect(mocks.post).not.toHaveBeenCalled()
    expect(mocks.message.warning).toHaveBeenCalled()

    randomReviewCount.value = -3
    await randomQualityCheck()
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('随机审核：没有待审核素材时给 info 而不是成功提示', async () => {
    mocks.post.mockResolvedValue({ data: { message: '', count: 0, task_id: undefined } })
    const { randomReviewCount, randomQualityCheck, reviewTask } = await load()
    randomReviewCount.value = 10

    await randomQualityCheck()

    expect(mocks.message.info).toHaveBeenCalledWith('没有待审核的素材')
    expect(reviewTask.value).toBeNull()
  })

  it('随机审核：正常提交后带 random 参数并开始轮询', async () => {
    mocks.post.mockResolvedValue({ data: { message: '', count: 8, task_id: 21 } })
    const { randomReviewCount, randomQualityCheck, reviewTask } = await load()
    randomReviewCount.value = 8

    await randomQualityCheck()

    expect(mocks.post).toHaveBeenCalledWith(
      '/ai/quality-check',
      null,
      expect.objectContaining({ params: { limit: 8, random: true } }),
    )
    expect(reviewTask.value?.id).toBe(21)
    expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('随机抽取 8'))
  })

  it('审核任务成功：按结果汇总提示，并刷新统计与未通过列表', async () => {
    const { triggerQualityCheck, reviewTask } = await load()
    mocks.post.mockResolvedValue({ data: { message: 'ok', count: 3, task_id: 9 } })

    await triggerQualityCheck()
    mocks.get.mockResolvedValue({
      data: {
        id: 9,
        status: 'success',
        result: { approved: 2, rejected: 1, pending: 0, ai_generated: 1 },
      },
    })
    await vi.advanceTimersByTimeAsync(1000) // 第一次轮询

    expect(mocks.message.success).toHaveBeenCalledWith(
      '审核完成：通过 2，拒绝 1，未判定 0，疑似 AI 1',
    )
    expect(mocks.loadRejectedItems).toHaveBeenCalled()
    expect(mocks.get).toHaveBeenCalledWith('/ai/quality-stats') // 统计刷新
    expect(reviewTask.value?.status).toBe('success')
  })

  it('审核任务失败：提示原因（不显示成功色）', async () => {
    const { triggerQualityCheck } = await load()
    mocks.post.mockResolvedValue({ data: { message: 'ok', count: 3, task_id: 9 } })

    await triggerQualityCheck()
    mocks.get.mockResolvedValue({
      data: { id: 9, status: 'failed', error: '显存不足' },
    })
    mocks.message.success.mockClear() // 忽略「已提交」那条成功提示
    await vi.advanceTimersByTimeAsync(1000)

    expect(mocks.message.error).toHaveBeenCalledWith('审核失败：显存不足')
    expect(mocks.message.success).not.toHaveBeenCalled()
  })

  it('轮询连续失败 5 次才停止（后端重启不该让进度卡死）', async () => {
    const { triggerQualityCheck } = await load()
    mocks.post.mockResolvedValue({ data: { message: 'ok', count: 3, task_id: 9 } })
    await triggerQualityCheck()

    mocks.get.mockRejectedValue(new Error('502'))
    await vi.advanceTimersByTimeAsync(1000) // 第 1 次失败
    for (let i = 0; i < 4; i++) {
      await vi.advanceTimersByTimeAsync(3000) // 失败重试间隔 3s
    }

    expect(mocks.message.error).toHaveBeenCalledWith(expect.stringContaining('多次失败'))
    mocks.get.mockClear()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mocks.get).not.toHaveBeenCalled() // 已停止
  })

  it('随机审核数量持久化到 localStorage（刷新后保持上次设置）', async () => {
    const { randomReviewCount } = await load()
    randomReviewCount.value = 33
    await Promise.resolve()

    expect(localStorage.getItem('review-random-count')).toBe('33')
  })
})
