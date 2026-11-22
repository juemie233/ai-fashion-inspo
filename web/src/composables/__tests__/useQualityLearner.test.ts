/**
 * 负样本初筛器（`useQualityLearner`）的测试。
 *
 * 训练/重置都是重操作，接口返回与状态机要严格对应：
 * - 训练接口返回 `{ error }` 时是「业务失败」：warning 而不是 success，且不刷新状态；
 * - 训练成功必须刷新状态（页面上的指标/样本数才会变）；
 * - 重置要返回 boolean 供父级决定是否提示/刷新，失败文案要保留可操作提示；
 * - loading / training / resetting 三个 busy 标记在在途期间为 true、结束后复位。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useQualityLearner, type LearnerStatus } from '../useQualityLearner'

function status(overrides: Partial<LearnerStatus> = {}): LearnerStatus {
  return {
    trained: true,
    model_path: 'models/quality_learner.pkl',
    threshold: 0.5,
    meta: {
      trained_at: '2026-11-20T10:00:00Z',
      sample_total: 100,
      positive: 60,
      negative: 40,
      dim: 512,
      threshold: 0.5,
      metrics: {
        accuracy: 0.9,
        precision: 0.88,
        recall: 0.86,
        f1: 0.87,
        false_reject_rate: 0.02,
        test_size: 20,
        confusion: { tn: 18, fp: 1, fn: 1, tp: 20 },
      },
    },
    dataset: { positive_total: 60, negative_total: 40 },
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

describe('useQualityLearner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: status() })
    mocks.post.mockResolvedValue({ data: {} })
  })

  describe('loadStatus', () => {
    it('成功回填状态并复位 loading', async () => {
      const learner = useQualityLearner()

      await learner.loadStatus()

      expect(mocks.get).toHaveBeenCalledWith('/ai/quality-learner/status')
      expect(learner.status.value?.trained).toBe(true)
      expect(learner.status.value?.meta?.sample_total).toBe(100)
      expect(learner.loading.value).toBe(false)
    })

    it('在途期间 loading=true', async () => {
      const pending = deferred<{ data: LearnerStatus }>()
      mocks.get.mockReturnValue(pending.promise)
      const learner = useQualityLearner()

      const promise = learner.loadStatus()
      expect(learner.loading.value).toBe(true)

      pending.resolve({ data: status() })
      await promise
      expect(learner.loading.value).toBe(false)
    })

    it('失败：透出 detail、状态保持原值、loading 复位', async () => {
      mocks.get.mockRejectedValueOnce({ response: { data: { detail: '模型文件损坏' } } })
      const learner = useQualityLearner()

      await learner.loadStatus()

      expect(mocks.message.error).toHaveBeenCalledWith('模型文件损坏')
      expect(learner.status.value).toBeNull()
      expect(learner.loading.value).toBe(false)
    })

    it('失败且无 detail：回退「获取初筛器状态失败」', async () => {
      mocks.get.mockRejectedValueOnce({})
      const learner = useQualityLearner()

      await learner.loadStatus()

      expect(mocks.message.error).toHaveBeenCalledWith('获取初筛器状态失败')
    })
  })

  describe('train', () => {
    it('成功：提示完成、刷新状态并复位 training', async () => {
      mocks.get.mockResolvedValueOnce({ data: status({ trained: true }) })
      const learner = useQualityLearner()

      await learner.train()

      expect(mocks.post).toHaveBeenCalledWith('/ai/quality-learner/train')
      expect(mocks.message.success).toHaveBeenCalledWith('初筛器训练完成')
      expect(mocks.get).toHaveBeenCalledWith('/ai/quality-learner/status')
      expect(learner.training.value).toBe(false)
    })

    it('接口返回 { error }：warning 提示业务原因，且不刷新状态', async () => {
      mocks.post.mockResolvedValue({ data: { error: '正负样本不足 20 条' } })
      const learner = useQualityLearner()

      await learner.train()

      expect(mocks.message.warning).toHaveBeenCalledWith('正负样本不足 20 条')
      expect(mocks.message.success).not.toHaveBeenCalled()
      expect(mocks.get).not.toHaveBeenCalled()
      expect(learner.training.value).toBe(false)
    })

    it('请求失败：透出 detail 并复位 training', async () => {
      mocks.post.mockRejectedValue({ response: { data: { detail: '训练进程崩溃' } } })
      const learner = useQualityLearner()

      await learner.train()

      expect(mocks.message.error).toHaveBeenCalledWith('训练进程崩溃')
      expect(mocks.get).not.toHaveBeenCalled()
      expect(learner.training.value).toBe(false)
    })

    it('请求失败且无 detail：回退「训练失败」', async () => {
      mocks.post.mockRejectedValue({})
      const learner = useQualityLearner()

      await learner.train()

      expect(mocks.message.error).toHaveBeenCalledWith('训练失败')
    })

    it('training 在在途期间为 true', async () => {
      const pending = deferred<{ data: unknown }>()
      mocks.post.mockReturnValue(pending.promise)
      const learner = useQualityLearner()

      const promise = learner.train()
      expect(learner.training.value).toBe(true)

      pending.resolve({ data: {} })
      await promise
      expect(learner.training.value).toBe(false)
    })
  })

  describe('reset', () => {
    it('成功：POST、提示回滚、刷新状态并返回 true', async () => {
      const learner = useQualityLearner()

      await expect(learner.reset()).resolves.toBe(true)

      expect(mocks.post).toHaveBeenCalledWith('/ai/quality-learner/reset')
      expect(mocks.message.success).toHaveBeenCalledWith('已删除初筛器模型，回滚到纯 VLM 审核')
      expect(mocks.get).toHaveBeenCalledWith('/ai/quality-learner/status')
      expect(learner.resetting.value).toBe(false)
    })

    it('失败：提示含「可能未配置 API Key」的兜底文案并返回 false', async () => {
      mocks.post.mockRejectedValue({})
      const learner = useQualityLearner()

      await expect(learner.reset()).resolves.toBe(false)

      expect(mocks.message.error).toHaveBeenCalledWith('重置失败（可能未配置 API Key）')
      expect(mocks.get).not.toHaveBeenCalled()
      expect(learner.resetting.value).toBe(false)
    })

    it('失败且后端有 detail：优先展示 detail', async () => {
      mocks.post.mockRejectedValue({ response: { data: { detail: '需要 API Key' } } })
      const learner = useQualityLearner()

      await expect(learner.reset()).resolves.toBe(false)

      expect(mocks.message.error).toHaveBeenCalledWith('需要 API Key')
    })

    it('resetting 在在途期间为 true', async () => {
      const pending = deferred<{ data: unknown }>()
      mocks.post.mockReturnValue(pending.promise)
      const learner = useQualityLearner()

      const promise = learner.reset()
      expect(learner.resetting.value).toBe(true)

      pending.resolve({ data: {} })
      await promise
      expect(learner.resetting.value).toBe(false)
    })
  })
})
