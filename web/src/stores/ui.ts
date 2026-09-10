/** UI 状态管理：侧边栏、通知、WebSocket 连接、素材打开模式。 */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

/** 素材打开方式：new_tab=新标签页打开（默认）/ current_tab=当前页跳转 */
export type MaterialOpenMode = 'new_tab' | 'current_tab'

/** 素材打开模式的 localStorage 键（与项目其它偏好键的 kebab 风格一致） */
const MATERIAL_OPEN_MODE_KEY = 'material-open-mode'

/** 读取持久化的打开模式（非法值回退默认「新标签页打开」） */
function readStoredOpenMode(): MaterialOpenMode {
  const raw = localStorage.getItem(MATERIAL_OPEN_MODE_KEY)
  return raw === 'current_tab' ? 'current_tab' : 'new_tab'
}

export const useUiStore = defineStore('ui', () => {
  /** 侧边栏是否收起 */
  const sidebarCollapsed = ref(false)
  /** WebSocket 连接状态 */
  const wsConnected = ref(false)
  /** 当前正在进行的 AI 分析数量 */
  const analyzingCount = ref(0)
  /** 素材打开模式（全局生效：素材库/搜索/合集/相似推荐等所有素材卡片的点击行为） */
  const materialOpenMode = ref<MaterialOpenMode>(readStoredOpenMode())

  // 偏好持久化：刷新或跨会话保留用户选择
  watch(materialOpenMode, (v) => {
    localStorage.setItem(MATERIAL_OPEN_MODE_KEY, v)
  })

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function setAnalyzingCount(count: number) {
    analyzingCount.value = count
  }

  function setMaterialOpenMode(mode: MaterialOpenMode) {
    materialOpenMode.value = mode
  }

  return {
    sidebarCollapsed,
    wsConnected,
    analyzingCount,
    materialOpenMode,
    toggleSidebar,
    setAnalyzingCount,
    setMaterialOpenMode,
  }
})
