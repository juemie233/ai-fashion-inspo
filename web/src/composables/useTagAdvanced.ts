/** 高级标签管理页状态编排：Tab 状态（URL 持久化）+ 全局批量编辑抽屉入口。
 *
 * 状态为模块级单例：管理视图与各子面板（如健康度面板）共用同一份
 * batchEditVisible / activeTab，避免各调一次 useTagAdvanced() 拿到独立 ref
 * 导致「打开抽屉」事件无法跨组件生效。
 */

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export type AdvancedTab = 'health' | 'cluster' | 'network' | 'effect' | 'tree' | 'history'

export const ADVANCED_TABS: AdvancedTab[] = [
  'health',
  'cluster',
  'network',
  'effect',
  'tree',
  'history',
]

// ── 模块级共享状态（单例）──
const batchEditVisible = ref(false)
const batchEditInitialTagIds = ref<number[]>([])
const batchEditInitialCategory = ref<string | undefined>(undefined)

/** 打开批量编辑抽屉（可选携带预选标签 / 预选类别） */
function openBatchEdit(opts: { tag_ids?: number[]; category?: string } = {}) {
  batchEditInitialTagIds.value = opts.tag_ids ?? []
  batchEditInitialCategory.value = opts.category
  batchEditVisible.value = true
}

export function useTagAdvanced() {
  const route = useRoute()
  const router = useRouter()

  /** 初始 Tab：URL 持久化，刷新后停留在原面板 */
  function initialTab(): AdvancedTab {
    const t = route.query.tab
    return t && ADVANCED_TABS.includes(t as AdvancedTab) ? (t as AdvancedTab) : 'health'
  }
  const activeTab = ref<AdvancedTab>(initialTab())

  watch(activeTab, (tab) => {
    const query = { ...route.query }
    if (tab === 'health') {
      delete query.tab
    } else {
      query.tab = tab
    }
    router.replace({ query })
  })

  return {
    activeTab,
    batchEditVisible,
    batchEditInitialTagIds,
    batchEditInitialCategory,
    openBatchEdit,
  }
}
