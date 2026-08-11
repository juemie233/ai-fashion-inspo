/** WebSocket 连接管理 composable。 */

import { ref, onMounted, onUnmounted } from 'vue'
import { useUiStore } from '@/stores/ui'

export function useWebSocket() {
  const uiStore = useUiStore()
  let ws: WebSocket | null = null
  let reconnectTimer: number | null = null

  function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws`

    ws = new WebSocket(url)

    ws.onopen = () => {
      uiStore.wsConnected = true
      console.log('[WS] 已连接')
    }

    ws.onclose = () => {
      uiStore.wsConnected = false
      console.log('[WS] 已断开')
      // 5 秒后重连
      reconnectTimer = window.setTimeout(connect, 5000)
    }

    ws.onerror = (err) => {
      console.error('[WS] 连接错误', err)
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'ai_analysis_done') {
          // 可在此处理分析完成的刷新逻辑
          console.log('[WS] AI 分析完成:', data.inspiration_id)
        }
      } catch {
        // 忽略非 JSON 消息
      }
    }
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
  }

  onMounted(connect)
  onUnmounted(disconnect)

  return { connected: uiStore.wsConnected }
}
