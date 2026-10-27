/** 页面标题（浏览器标签页）：负责「数据加载后才知道」的标题段落。
 *
 * 为什么要它：人物详情要显示「人物管理-某博主的网名」、收藏合集要显示「收藏合集-合集名」，
 * 这些名字只能在接口返回后拿到，静态路由表（`utils/pageTitle.ts`）无法表达。
 *
 * 与路由守卫的分工：URL 驱动的子页面由守卫统一设置；本 composable 只做运行时覆盖，
 * 且源变化时自动重算（人物名加载完成那一刻标题就从「人物管理」变成「人物管理-某博主」）。
 */

import { toValue, watchEffect, type MaybeRefOrGetter } from 'vue'
import { formatDocumentTitle } from '@/utils/pageTitle'

/**
 * @param segment 标题段落（如 `人物管理-某博主`）；空值回退为站点名
 */
export function usePageTitle(segment: MaybeRefOrGetter<string | null | undefined>): void {
  watchEffect(() => {
    document.title = formatDocumentTitle(toValue(segment))
  })
}
