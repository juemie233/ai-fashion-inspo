/** 采集专用 Chrome 生命周期：启动 / 停止 / 状态刷新。 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { ChromeStatus } from '@/types/scraper'
import { getApiErrorMessage } from '@/utils/apiError'

/** 状态 → 中文徽标文案 */
export const CHROME_STATE_LABELS: Record<string, string> = {
  running: '已连接',
  not_started: '未启动',
  port_conflict: '端口冲突',
  starting: '启动中',
}

/** 状态 → Arco 标签预设色（naive 语义色，由使用方映射） */
export const CHROME_STATE_TAG: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  running: 'success',
  not_started: 'default',
  port_conflict: 'error',
  starting: 'warning',
}

export function useChromeManager() {
  
  const chromeStatus = ref<ChromeStatus | null>(null)
  /** 启动/停止操作进行中，用于按钮 loading 态 */
  const chromeBusy = ref(false)

  async function refreshChromeStatus() {
    try {
      const r = await apiClient.get('/scraper/chrome/status')
      chromeStatus.value = r.data
    } catch {
      chromeStatus.value = null
    }
  }

  async function startChrome() {
    chromeBusy.value = true
    try {
      const r = await apiClient.post('/scraper/chrome/start')
      chromeStatus.value = r.data
      if (r.data.state === 'running') Message.success('Chrome 已启动')
      else if (r.data.state === 'port_conflict') Message.error(r.data.detail || '端口冲突，请关闭占用进程')
      else Message.warning(r.data.detail || '启动失败')
    } catch (e) {
      Message.error('启动 Chrome 失败: ' + getApiErrorMessage(e, '操作失败'))
    } finally {
      chromeBusy.value = false
    }
  }

  async function stopChrome() {
    chromeBusy.value = true
    try {
      const r = await apiClient.post('/scraper/chrome/stop')
      chromeStatus.value = r.data
      Message.success('Chrome 已停止')
    } catch (e) {
      Message.error('停止 Chrome 失败: ' + getApiErrorMessage(e, '操作失败'))
    } finally {
      chromeBusy.value = false
    }
  }

  return { chromeStatus, chromeBusy, refreshChromeStatus, startChrome, stopChrome }
}
