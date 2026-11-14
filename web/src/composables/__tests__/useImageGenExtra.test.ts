/**
 * 本地出图状态机（`useImageGen`）的历史区/取消/重跑/标注等次要动作测试。
 *
 * `useImageGen.test.ts` 已覆盖提交→轮询→候选→入库主链路；本文件补历史区与周边动作，
 * 它们的共性是**用户以为操作没生效**或**列表与后端不一致**：
 * - 历史翻页/筛选必须回第 1 页，否则停在旧页看到空列表；
 * - 收藏筛选下取消收藏后必须重拉（这行已不符合条件，留着会让 total 对不上）；
 * - 多姿态合并行的标注更新要保留 run_ids/candidates，否则整组被「打散成一张图」；
 * - 取消/重跑要刷新当前详情并停/启轮询；同参重跑必须新建批次（历史保留）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  cancelRun: vi.fn(),
  confirmImport: vi.fn(),
  createRun: vi.fn(),
  discardRun: vi.fn(),
  fetchRun: vi.fn(),
  fetchStatus: vi.fn(),
  listRuns: vi.fn(),
  previewPrompt: vi.fn(),
  regenerateCandidate: vi.fn(),
  retryRun: vi.fn(),
  fetchRunStats: vi.fn(),
  updateRunMetaApi: vi.fn(),
  polling: { start: vi.fn(), stop: vi.fn(), callback: null as null | (() => Promise<void>) },
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/generate', () => ({
  cancelRun: mocks.cancelRun,
  confirmImport: mocks.confirmImport,
  createRun: mocks.createRun,
  discardRun: mocks.discardRun,
  fetchRun: mocks.fetchRun,
  fetchStatus: mocks.fetchStatus,
  listRuns: mocks.listRuns,
  previewPrompt: mocks.previewPrompt,
  regenerateCandidate: mocks.regenerateCandidate,
  retryRun: mocks.retryRun,
  fetchRunStats: mocks.fetchRunStats,
  updateRunMeta: mocks.updateRunMetaApi,
}))

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

import { useImageGen } from '../useImageGen'

function detail(runOverrides: Record<string, unknown> = {}, items: unknown[] = []) {
  return {
    run: {
      id: 1,
      status: 'running',
      done: 0,
      total: 4,
      failed: 0,
      error: null,
      ...runOverrides,
    },
    items,
    task: null,
  }
}

describe('useImageGen（历史区与周边动作）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.listRuns.mockResolvedValue({ items: [], total: 0, keep_days: 7 })
    mocks.fetchRun.mockResolvedValue(detail())
    mocks.cancelRun.mockResolvedValue({})
    mocks.retryRun.mockResolvedValue({ run_id: 9, seed: 42 })
    mocks.updateRunMetaApi.mockImplementation(async (id: number, patch: object) => ({
      id,
      favorite: false,
      title: '',
      ...patch,
    }))
    mocks.previewPrompt.mockResolvedValue({ prompt: '米色针织 + 直筒牛仔裤' })
    mocks.fetchRunStats.mockResolvedValue({ runs: 3, images: 10 })
  })

  describe('loadStatus', () => {
    it('成功写入状态并缓存档位元数据', async () => {
      mocks.fetchStatus.mockResolvedValue({ comfyui_ready: true, models_meta: { version: 1 } })
      const gen = useImageGen()

      await gen.loadStatus()

      expect(gen.status.value).toEqual({ comfyui_ready: true, models_meta: { version: 1 } })
      expect(gen.statusLoading.value).toBe(false)
    })

    it('失败提示并清空状态', async () => {
      mocks.fetchStatus.mockRejectedValue(null)
      const gen = useImageGen()

      await gen.loadStatus()

      expect(mocks.message.error).toHaveBeenCalledWith('读取出图环境失败')
      expect(gen.status.value).toBeNull()
    })
  })

  describe('历史列表', () => {
    it('loadHistory 带分页与筛选请求后端并写入结果', async () => {
      mocks.listRuns.mockResolvedValue({
        items: [{ id: 1, title: '批次一' }],
        total: 42,
        keep_days: 7,
      })
      const gen = useImageGen()
      gen.historyFilters.value = { status: 'done' }

      await gen.loadHistory()

      expect(mocks.listRuns).toHaveBeenCalledWith({
        page: 1,
        size: 20,
        status: 'done',
      })
      expect(gen.historyTotal.value).toBe(42)
      expect(gen.historyKeepDays.value).toBe(7)
      expect(gen.historyLoading.value).toBe(false)
    })

    it('loadHistory 失败提示且 loading 复位', async () => {
      mocks.listRuns.mockRejectedValue(null)
      const gen = useImageGen()

      await gen.loadHistory()

      expect(mocks.message.error).toHaveBeenCalledWith('读取生成历史失败')
      expect(gen.historyLoading.value).toBe(false)
    })

    it('翻页最小为 1，并按新页拉取', async () => {
      const gen = useImageGen()
      await gen.setHistoryPage(3)
      expect(gen.historyPage.value).toBe(3)

      await gen.setHistoryPage(-5)
      expect(gen.historyPage.value).toBe(1)
      expect(mocks.listRuns).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }))
    })

    it('改筛选一律回第 1 页（避免停在旧页看到空列表）', async () => {
      const gen = useImageGen()
      await gen.setHistoryPage(4)

      await gen.setHistoryFilters({ model_key: 'z_image_int8' })

      expect(gen.historyPage.value).toBe(1)
      expect(mocks.listRuns).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, model_key: 'z_image_int8' }),
      )
    })

    it('统计卡失败静默（保留上一次数据）', async () => {
      const gen = useImageGen()
      await gen.loadHistoryStats(7)
      expect(gen.historyStats.value).toEqual({ runs: 3, images: 10 })
      expect(mocks.fetchRunStats).toHaveBeenCalledWith(7)

      mocks.fetchRunStats.mockRejectedValue(new Error('500'))
      await gen.loadHistoryStats()
      expect(gen.historyStats.value).toEqual({ runs: 3, images: 10 }) // 未被清空
      expect(mocks.message.error).not.toHaveBeenCalled()
    })
  })

  describe('updateRunMeta（收藏/命名/备注）', () => {
    it('成功原地替换列表行，不整页刷新', async () => {
      const gen = useImageGen()
      gen.history.value = [{ id: 3, title: '旧名', favorite: false } as never]

      const ok = await gen.updateRunMeta(3, { title: '新名' })

      expect(ok).toBe(true)
      expect(gen.history.value[0].title).toBe('新名')
      expect(mocks.listRuns).not.toHaveBeenCalled() // 原地替换而非刷新
    })

    it('多姿态合并行保留 run_ids/candidates，不能被更新打散', async () => {
      const gen = useImageGen()
      gen.history.value = [
        {
          id: 3,
          group_id: 'g1',
          run_ids: [3, 4],
          candidates: [{ filename: 'a.png' }],
          candidate_total: 1,
          title: '旧名',
        } as never,
      ]

      await gen.updateRunMeta(3, { title: '新名' })

      const row = gen.history.value[0] as unknown as Record<string, unknown>
      expect(row.title).toBe('新名')
      expect(row.group_id).toBe('g1')
      expect(row.run_ids).toEqual([3, 4])
      expect(row.candidates).toEqual([{ filename: 'a.png' }])
    })

    it('收藏筛选下状态不再匹配时重拉当前页', async () => {
      mocks.updateRunMetaApi.mockResolvedValue({ id: 3, favorite: false, title: '' })
      const gen = useImageGen()
      gen.historyFilters.value = { favorite: true }
      gen.history.value = [{ id: 3, favorite: true } as never]

      await gen.updateRunMeta(3, { favorite: false })

      expect(mocks.listRuns).toHaveBeenCalled() // 这行已不符合筛选，重拉
    })

    it('失败返回 false 并提示', async () => {
      mocks.updateRunMetaApi.mockRejectedValue(null)
      const gen = useImageGen()

      const ok = await gen.updateRunMeta(3, { title: 'x' })

      expect(ok).toBe(false)
      expect(mocks.message.error).toHaveBeenCalledWith('保存标注失败')
    })
  })

  describe('取消', () => {
    it('单批次：请求取消、刷新详情并停止轮询', async () => {
      const gen = useImageGen()
      gen.runId.value = 5

      await gen.cancel()

      expect(mocks.cancelRun).toHaveBeenCalledWith(5)
      expect(mocks.message.info).toHaveBeenCalledWith('已请求取消（已生成的图会保留在候选区）')
      expect(mocks.fetchRun).toHaveBeenCalledWith(5) // 刷新当前详情
      expect(mocks.polling.stop).toHaveBeenCalled()
    })

    it('多姿态组：逐个批次取消', async () => {
      const gen = useImageGen()
      gen.groupRunIds.value = [1, 2]

      await gen.cancel()

      expect(mocks.cancelRun).toHaveBeenNthCalledWith(1, 1)
      expect(mocks.cancelRun).toHaveBeenNthCalledWith(2, 2)
    })

    it('没有当前批次时不发请求', async () => {
      const gen = useImageGen()
      await gen.cancel()
      expect(mocks.cancelRun).not.toHaveBeenCalled()
    })

    it('取消失败提示「取消失败」', async () => {
      mocks.cancelRun.mockRejectedValue(null)
      const gen = useImageGen()
      gen.runId.value = 5

      await gen.cancel()

      expect(mocks.message.error).toHaveBeenCalledWith('取消失败')
    })
  })

  describe('同参重跑 / 单张重出 / 反推描述', () => {
    it('重跑成功：切到新批次、提示 seed、刷新并启轮询', async () => {
      const gen = useImageGen()

      const id = await gen.retry(3, 0)

      expect(id).toBe(9)
      expect(gen.runId.value).toBe(9)
      expect(gen.lastSeed.value).toBe(42)
      expect(mocks.message.success).toHaveBeenCalledWith('已按原参数重跑（seed 42）')
      expect(mocks.polling.start).toHaveBeenCalled()
      expect(mocks.listRuns).toHaveBeenCalled()
    })

    it('云端 seed 为 null 时换「云端每次随机」文案', async () => {
      mocks.retryRun.mockResolvedValue({ run_id: 9, seed: null })
      const gen = useImageGen()

      await gen.retry(3)

      expect(mocks.message.success).toHaveBeenCalledWith('已按原参数重新生成（云端每次随机）')
    })

    it('重跑失败返回 null 并提示', async () => {
      mocks.retryRun.mockRejectedValue(null)
      const gen = useImageGen()

      expect(await gen.retry(3)).toBeNull()
      expect(mocks.message.error).toHaveBeenCalledWith('重跑失败')
      expect(gen.submitting.value).toBe(false)
    })

    it('promptFrom：空列表直接 null、成功返回描述、失败提示', async () => {
      const gen = useImageGen()

      expect(await gen.promptFrom([])).toBeNull()
      expect(mocks.previewPrompt).not.toHaveBeenCalled()

      expect(await gen.promptFrom(['i1'])).toBe('米色针织 + 直筒牛仔裤')

      mocks.previewPrompt.mockRejectedValue(null)
      expect(await gen.promptFrom(['i1'])).toBeNull()
      expect(mocks.message.error).toHaveBeenCalledWith('从素材反推描述失败')
    })
  })

  describe('重置', () => {
    it('reset 清空批次/组/选择/seed', async () => {
      const gen = useImageGen()
      gen.runId.value = 5
      gen.lastSeed.value = 7
      gen.selected.value = new Set(['k'])
      gen.groupRunIds.value = [5]

      gen.reset()

      expect(gen.runId.value).toBeNull()
      expect(gen.lastSeed.value).toBeNull()
      expect(gen.selected.value.size).toBe(0)
      expect(gen.groupRunIds.value).toEqual([])
    })
  })
})
