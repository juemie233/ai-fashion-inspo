/** 素材详情页的「上一张/下一张」浏览上下文。
 *
 * 从进入详情时携带的列表筛选 query 重建「同一次浏览」的相邻素材，
 * 让用户无需回到列表即可连续刷图；当前素材不在上下文列表时隐藏导航。
 * 自成一体的状态 + 逻辑（与 useSimilarItems 同模式），供 DetailView 编排使用。
 */

import { computed, ref, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

import {
  fetchInspirations,
  type InspirationDetailOut,
  type InspirationOut,
} from '@/api/inspirations'
import { buildBrowseParams, storedBrowsePageSize } from '@/utils/browseQuery'

export function useBrowseContext(opts: {
  /** 当前详情对象（由视图持有，浏览上下文据此定位当前素材位置） */
  detail: Ref<InspirationDetailOut | null>
  route: RouteLocationNormalizedLoaded
  router: Router
}) {
  const { detail, route, router } = opts
  
  const browseItems = ref<InspirationOut[]>([])
  const browseTotal = ref(0)
  const browsePage = ref(parseInt(route.query.page as string) || 1)
  const browseLoading = ref(false)
  /** 上下文请求序号：详情切换 / reset 后使旧响应失效，避免跨详情串数据 */
  let ctxSeq = 0

  /** 当前素材在浏览列表中的位置（不在列表中返回 -1，隐藏导航） */
  const browseIndex = computed(() => {
    if (!detail.value) return -1
    return browseItems.value.findIndex((i) => i.id === detail.value!.id)
  })

  /** 全局位置（跨页）：(页码-1)×每页 + 页内位置 + 1 */
  const browsePosition = computed(() => {
    if (browseIndex.value < 0) return 0
    return (browsePage.value - 1) * storedBrowsePageSize() + browseIndex.value + 1
  })

  /** 页内是否可前进/后退（跨页由 goNeighbor 翻页补齐） */
  const hasPrev = computed(
    () => browseIndex.value > 0 || (browseIndex.value === 0 && browsePage.value > 1),
  )
  const hasNext = computed(() => {
    if (browseIndex.value < 0) return false
    const size = storedBrowsePageSize()
    return (
      browseIndex.value < browseItems.value.length - 1 || browsePage.value * size < browseTotal.value
    )
  })

  /** 详情重新加载时调用：清空上下文并使在途请求失效 */
  function reset() {
    ctxSeq++
    browseItems.value = []
  }

  /** 加载当前筛选条件下的上下文列表（页码从 route.query 恢复，与浏览导航保持一致） */
  async function load() {
    const page = parseInt(route.query.page as string) || 1
    const seq = ++ctxSeq
    browseLoading.value = true
    browsePage.value = page
    try {
      const data = await fetchInspirations(
        buildBrowseParams(route.query as Record<string, string>, page, storedBrowsePageSize()),
      )
      if (seq !== ctxSeq) return
      browseItems.value = data.items
      browseTotal.value = data.total
    } catch {
      // 上下文加载失败：静默隐藏导航，不影响详情主流程
      if (seq === ctxSeq) browseItems.value = []
    } finally {
      if (seq === ctxSeq) browseLoading.value = false
    }
  }

  /** 跳转到指定素材，保持浏览 query（页码同步更新） */
  function gotoItem(id: string, page?: number) {
    const query = { ...route.query }
    if (page !== undefined) {
      if (page > 1) query.page = String(page)
      else delete query.page
    }
    router.replace({ path: `/detail/${id}`, query })
  }

  /** 上一张/下一张：页内移动；到页边界时自动翻页取相邻页的首/尾素材 */
  async function goNeighbor(dir: 'prev' | 'next') {
    if (!detail.value || browseIndex.value < 0 || browseLoading.value) return
    const size = storedBrowsePageSize()
    if (dir === 'prev') {
      if (browseIndex.value > 0) {
        gotoItem(browseItems.value[browseIndex.value - 1].id)
      } else if (browsePage.value > 1) {
        const page = browsePage.value - 1
        try {
          const data = await fetchInspirations(
            buildBrowseParams(route.query as Record<string, string>, page, size),
          )
          if (data.items.length > 0) gotoItem(data.items[data.items.length - 1].id, page)
          else Message.info('前面没有更多素材了')
        } catch {
          Message.error('加载上一页失败')
        }
      }
    } else {
      if (browseIndex.value < browseItems.value.length - 1) {
        gotoItem(browseItems.value[browseIndex.value + 1].id)
      } else if (browsePage.value * size < browseTotal.value) {
        const page = browsePage.value + 1
        try {
          const data = await fetchInspirations(
            buildBrowseParams(route.query as Record<string, string>, page, size),
          )
          if (data.items.length > 0) gotoItem(data.items[0].id, page)
          else Message.info('后面没有更多素材了')
        } catch {
          Message.error('加载下一页失败')
        }
      }
    }
  }

  return {
    browseItems,
    browseTotal,
    browsePage,
    browseLoading,
    browseIndex,
    browsePosition,
    hasPrev,
    hasNext,
    reset,
    load,
    gotoItem,
    goNeighbor,
  }
}
