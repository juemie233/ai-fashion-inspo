/**
 * 素材详情打开入口（全局统一）：按「素材打开模式」偏好决定新标签页打开还是当前页跳转。
 *
 * 偏好存于 ui store（localStorage 持久化），素材库/搜索/收藏合集/相似推荐/采集结果等
 * 所有素材卡片与素材入口都应走本函数，保证设置全局生效、行为一致。
 */

import type { LocationQueryRaw, RouteLocationRaw, Router } from 'vue-router'

import { useUiStore } from '@/stores/ui'
import { openInNewTab } from '@/utils/openInNewTab'

/**
 * 打开素材详情。
 *
 * @param router: 组件内的 router 实例（useRouter()）
 * @param id: 素材 ID
 * @param query: 附加查询参数（如素材库的筛选上下文，用于详情页返回时恢复）
 * @returns 是否在新标签页打开（false = 当前页跳转）
 */
export function openInspiration(router: Router, id: string, query?: LocationQueryRaw): boolean {
  const ui = useUiStore()
  const target: RouteLocationRaw = { name: 'detail', params: { id }, query }

  if (ui.materialOpenMode === 'current_tab') {
    void router.push(target)
    return false
  }

  // 新标签页模式：用 resolve 生成完整 href（含 query），保留浏览上下文
  const { href } = router.resolve(target)
  if (openInNewTab(href)) return true
  // 被浏览器弹窗拦截时降级为当前页跳转，避免「点了没反应」
  void router.push(target)
  return false
}
