/**
 * GPU 显存监控（`useGpuMonitor`）的测试。
 *
 * 这条链路同时被「定时轮询」和「手动卸载模型」驱动，出错的表现是用户看不到真实显存：
 * - 拉取失败必须**静默**（GPU 不可用/Ollama 未连时不打断界面）；
 * - 只有 GPU 可用才追加趋势点（否则会画出一串 0）；
 * - 趋势序列按 historySize 截断，长跑页面不能无限涨内存；
 * - 轮询 start 立即拉一次、可重复 start 不重建定时器、stop 后不再拉；
 * - 卸载走原生 fetch（非 axios），非 2xx 要把后端 detail 透出，成功后延迟 2s 刷新。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, defaults: { baseURL: '/api' } },
}))

import { useGpuMonitor, type GpuStats } from '../useGpuMonitor'

function stats(overrides: Partial<GpuStats> = {}): GpuStats {
  return {
    gpu_available: true,
    gpu_name: 'NVIDIA RTX 4090',
    total_vram_mb: 24564,
    used_vram_mb: 8192,
    free_vram_mb: 16372,
    usage_percent: 33.4,
    loaded_models: [{ name: 'qwen3-vl:8b', vram_mb: 8192, loaded_at: null }],
    ...overrides,
  }
}

function okFetch() {
  return vi.fn().mockResolvedValue({ ok: true })
}

describe('useGpuMonitor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    // 固定系统时间：趋势点的 HH:MM:SS 才有确定断言（vitest 已固定 TZ=Asia/Shanghai）
    vi.setSystemTime(new Date('2026-11-19T10:20:30'))
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  describe('loadGpuStats', () => {
    it('GPU 可用：回填统计并追加一条趋势点（时间为本地 HH:MM:SS）', async () => {
      mocks.get.mockResolvedValue({ data: stats({ used_vram_mb: 9000 }) })
      const gpu = useGpuMonitor()

      await gpu.loadGpuStats()

      expect(mocks.get).toHaveBeenCalledWith('/ai/gpu-stats')
      expect(gpu.gpuStats.value).toEqual(stats({ used_vram_mb: 9000 }))
      expect(gpu.gpuHistory.value).toEqual([{ time: '10:20:30', used_mb: 9000, total_mb: 24564 }])
    })

    it('GPU 不可用：统计照常回填，但不追加趋势点', async () => {
      mocks.get.mockResolvedValue({
        data: stats({ gpu_available: false, used_vram_mb: 0, total_vram_mb: 0 }),
      })
      const gpu = useGpuMonitor()

      await gpu.loadGpuStats()

      expect(gpu.gpuStats.value?.gpu_available).toBe(false)
      expect(gpu.gpuHistory.value).toEqual([])
    })

    it('请求失败静默：不抛错、不污染统计', async () => {
      mocks.get.mockRejectedValue(new Error('Ollama 未启动'))
      const gpu = useGpuMonitor()

      await expect(gpu.loadGpuStats()).resolves.toBeUndefined()
      expect(gpu.gpuStats.value).toBeNull()
      expect(gpu.gpuHistory.value).toEqual([])
    })

    it('趋势序列按 historySize 截断，只保留最近 N 个点', async () => {
      mocks.get
        .mockResolvedValueOnce({ data: stats({ used_vram_mb: 1000 }) })
        .mockResolvedValueOnce({ data: stats({ used_vram_mb: 2000 }) })
        .mockResolvedValueOnce({ data: stats({ used_vram_mb: 3000 }) })
      const gpu = useGpuMonitor(5000, 2)

      await gpu.loadGpuStats()
      await gpu.loadGpuStats()
      await gpu.loadGpuStats()

      expect(gpu.gpuHistory.value.map((p) => p.used_mb)).toEqual([2000, 3000])
    })
  })

  describe('轮询', () => {
    it('start 立即拉一次，之后按间隔拉取；stop 后不再拉', async () => {
      mocks.get.mockResolvedValue({ data: stats() })
      const gpu = useGpuMonitor(5000)

      gpu.startPolling()
      expect(mocks.get).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(5000)
      expect(mocks.get).toHaveBeenCalledTimes(2)

      gpu.stopPolling()
      await vi.advanceTimersByTimeAsync(15000)
      expect(mocks.get).toHaveBeenCalledTimes(2)
    })

    it('重复 start 幂等：不会叠加定时器（每拍只拉一次）', async () => {
      mocks.get.mockResolvedValue({ data: stats() })
      const gpu = useGpuMonitor(5000)

      gpu.startPolling()
      gpu.startPolling()
      expect(mocks.get).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(5000)
      expect(mocks.get).toHaveBeenCalledTimes(2)
    })
  })

  describe('unloadModel', () => {
    it('卸载成功：提示后延迟 2s 刷新显存', async () => {
      const fetchMock = okFetch()
      vi.stubGlobal('fetch', fetchMock)
      mocks.get.mockResolvedValue({ data: stats() })
      const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
      const gpu = useGpuMonitor()

      const ok = await gpu.unloadModel('模型A')

      expect(ok).toBe(true)
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/ai/unload-model?model_name=%E6%A8%A1%E5%9E%8BA',
        { method: 'POST' },
      )
      expect(success).toHaveBeenCalledWith('正在卸载 模型A...')
      // 卸载后立即刷新会读到还没释放完的显存，所以等 2s
      expect(mocks.get).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(2000)
      expect(mocks.get).toHaveBeenCalledWith('/ai/gpu-stats')
    })

    it('非 2xx：透出后端 detail 并立即刷新一次', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: '模型未加载' }),
      })
      vi.stubGlobal('fetch', fetchMock)
      mocks.get.mockResolvedValue({ data: stats() })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const gpu = useGpuMonitor()

      const ok = await gpu.unloadModel('模型B')

      expect(ok).toBe(false)
      expect(error).toHaveBeenCalledWith('模型未加载')
      // 失败也刷新：让用户看到当前真实显存
      expect(mocks.get).toHaveBeenCalledTimes(1)
    })

    it('非 2xx 且响应体缺 detail：回退 HTTP 状态码', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
      )
      mocks.get.mockResolvedValue({ data: stats() })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const gpu = useGpuMonitor()

      await gpu.unloadModel('模型E')

      expect(error).toHaveBeenCalledWith('HTTP 500')
    })

    it('非 2xx 且响应体不是 JSON：回退 HTTP 状态码', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: false,
          status: 503,
          json: () => Promise.reject(new Error('invalid json')),
        }),
      )
      mocks.get.mockResolvedValue({ data: stats() })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const gpu = useGpuMonitor()

      await gpu.unloadModel('模型C')

      expect(error).toHaveBeenCalledWith('HTTP 503')
    })

    it('网络异常：用兜底文案提示并刷新', async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
      mocks.get.mockResolvedValue({ data: stats() })
      const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
      const gpu = useGpuMonitor()

      const ok = await gpu.unloadModel('模型D')

      expect(ok).toBe(false)
      // getApiErrorMessage 优先取 error.message，取不到才用兜底
      expect(error).toHaveBeenCalledWith('Failed to fetch')
      expect(mocks.get).toHaveBeenCalledTimes(1)
    })
  })
})
