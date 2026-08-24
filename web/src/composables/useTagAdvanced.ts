/** 高级标签管理页状态编排：Tab 状态（URL 持久化）+ 全局批量编辑抽屉入口。 */

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

  // ── 全局批量编辑抽屉：可从任意面板携带初始范围打开 ──
  const batchEditVisible = ref(false)
  const batchEditInitialTagIds = ref<number[]>([])
  const batchEditInitialCategory = ref<string | undefined>(undefined)

  /** 打开批量编辑抽屉（可选携带预选标签 / 预选类别） */
  function openBatchEdit(opts: { tag_ids?: number[]; category?: string } = {}) {
    batchEditInitialTagIds.value = opts.tag_ids ?? []
    batchEditInitialCategory.value = opts.category
    batchEditVisible.value = true
  }

  return {
    activeTab,
    batchEditVisible,
    batchEditInitialTagIds,
    batchEditInitialCategory,
    openBatchEdit,
  }
}
