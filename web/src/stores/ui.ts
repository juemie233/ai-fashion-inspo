/** UI 状态管理：侧边栏、通知、WebSocket 连接。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  /** 侧边栏是否收起 */
  const sidebarCollapsed = ref(false)
  /** WebSocket 连接状态 */
  const wsConnected = ref(false)
  /** 当前正在进行的 AI 分析数量 */
  const analyzingCount = ref(0)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function setAnalyzingCount(count: number) {
    analyzingCount.value = count
  }

  return {
    sidebarCollapsed,
    wsConnected,
    analyzingCount,
    toggleSidebar,
    setAnalyzingCount,
  }
})
