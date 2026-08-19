/** GPU 显存监控 composable：定时轮询 + 短时趋势数据（供 echarts 趋势图使用）。 */

import { ref, onBeforeUnmount } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'

/** GPU 显存统计（与后端 /ai/gpu-stats 对齐） */
export interface GpuStats {
  gpu_available: boolean
  gpu_name: string
  total_vram_mb: number
  used_vram_mb: number
  free_vram_mb: number
  usage_percent: number
  loaded_models: Array<{ name: string; vram_mb: number; loaded_at: string | null }>
}

/** GPU 趋势点（用于 echarts 折线图） */
export interface GpuTrendPoint {
  time: string
  used_mb: number
  total_mb: number
}

export function useGpuMonitor(pollIntervalMs = 5000, historySize = 60) {
    const gpuStats = ref<GpuStats | null>(null)
  const gpuHistory = ref<GpuTrendPoint[]>([])
  let timer: ReturnType<typeof setInterval> | null = null

  /** 拉取一次 GPU 显存统计并追加趋势点 */
  async function loadGpuStats() {
    try {
      const { data } = await apiClient.get<GpuStats>('/ai/gpu-stats')
      gpuStats.value = data
      if (data.gpu_available) {
        const now = new Date()
        const pad = (n: number) => n.toString().padStart(2, '0')
        const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
        gpuHistory.value = [
          ...gpuHistory.value,
          { time, used_mb: data.used_vram_mb, total_mb: data.total_vram_mb },
        ].slice(-historySize)
      }
    } catch {
      /* 静默失败：GPU 不可用或 Ollama 未连接时不打断界面 */
    }
  }

  /** 卸载模型释放显存，成功后延迟刷新 */
  async function unloadModel(name: string): Promise<boolean> {
    try {
      const baseUrl = apiClient.defaults.baseURL || ''
      const resp = await fetch(`${baseUrl}/ai/unload-model?model_name=${encodeURIComponent(name)}`, { method: 'POST' })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      Message.success(`正在卸载 ${name}...`)
      setTimeout(loadGpuStats, 2000)
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '卸载模型失败'))
      loadGpuStats()
      return false
    }
  }

  /** 开始定时轮询（先立即拉一次） */
  function startPolling() {
    stopPolling()
    loadGpuStats()
    timer = setInterval(loadGpuStats, pollIntervalMs)
  }

  /** 停止轮询 */
  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onBeforeUnmount(stopPolling)

  return { gpuStats, gpuHistory, loadGpuStats, unloadModel, startPolling, stopPolling }
}
