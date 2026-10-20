/** 合集内素材的「编辑排序」模式（从 CollectionsView.vue 抽出，F 批）。
 *
 * 覆盖：进入排序模式（含成员数守卫）→ 全量拉取成员 → 缩略图条带拖拽重排 → 保存顺序。
 *
 * 为什么抽：视图已过 800 行；这段是「自成体系的状态 + 逻辑」，按 CLAUDE.md
 * 「前端文件拆分约定」应放 composables。视图只保留接线，模板不动。
 *
 * 拖拽中的成员 ID 同样是普通 `let` + 包装函数：理由见 `onItemDragStart` 的注释。
 */

import { ref, type ComputedRef, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import {
  fetchCollectionInspirations,
  reorderCollectionItems,
  type CollectionOut,
} from '@/api/collections'
import type { InspirationOut } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'

/** 排序模式一次拉取的成员上限（超过这个量在界面上编排没有意义） */
const ORDERING_ITEM_LIMIT = 500

/** 成员数超过这个值时拒绝进入排序模式（分页瀑布流下编排容易误操作） */
const ORDERING_MAX_ITEMS = 200

interface CollectionItemOrderingDeps {
  /** 当前选中的合集（null = 未选） */
  current: ComputedRef<CollectionOut | null>
  /** 当前合集的成员总数（用于「太多了，先筛选」的守卫） */
  total: Ref<number>
  /** 保存成功后刷新内容区 */
  loadContent: () => void | Promise<void>
}

export function useCollectionItemOrdering(opts: CollectionItemOrderingDeps) {
  const { current, total, loadContent } = opts

  const ordering = ref(false)
  // 拖拽中的成员 ID：只在 dragstart → drop 之间传递，不需要响应式，
  // 所以用普通变量（**并且在模板里通过函数赋值**，见下）
  let draggingItemId: string | null = null

  /**
   * 记录正在拖拽的成员。
   *
   * 为什么不在模板里直接写 `@dragstart="draggingItemId = it.id"`：那是全项目唯一一处
   * 「模板内给普通 let 变量赋值」的写法，IDE 的 Vue 语言服务会把该绑定按初始值推断成
   * `null` 从而报 2322（`不能将类型"string"分配给类型"null"`），而 vue-tsc 命令行不报
   * ——两边口径不一致。包一层函数既消除歧义，也与合集拖拽（onCollectionDragStart）一致。
   */
  function onItemDragStart(id: string) {
    draggingItemId = id
  }

  const orderedItems = ref<InspirationOut[]>([])

  async function loadAllForOrdering() {
    const c = current.value
    if (!c) return
    try {
      const data = await fetchCollectionInspirations(c.id, {
        page: 1,
        size: ORDERING_ITEM_LIMIT,
      })
      orderedItems.value = data.items
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载成员失败'))
    }
  }

  function enterOrdering() {
    if (total.value > ORDERING_MAX_ITEMS) {
      Message.warning('素材过多，建议先用筛选精简后再编排顺序')
      return
    }
    ordering.value = true
    // 排序模式需要全量成员（分页瀑布流不便于编排），一次拉取
    void loadAllForOrdering()
  }

  async function onItemDrop(targetId: string) {
    const from = draggingItemId
    draggingItemId = null
    if (!from || from === targetId) return
    const ids = orderedItems.value.map((i) => i.id)
    const fromIdx = ids.indexOf(from)
    const toIdx = ids.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    ids.splice(toIdx, 0, ids.splice(fromIdx, 1)[0])
    orderedItems.value = ids
      .map((id) => orderedItems.value.find((i) => i.id === id))
      .filter((i): i is InspirationOut => !!i)
  }

  async function saveItemOrder() {
    const c = current.value
    if (!c) return
    try {
      await reorderCollectionItems(
        c.id,
        orderedItems.value.map((i) => i.id),
      )
      Message.success('顺序已保存')
      ordering.value = false
      loadContent()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存顺序失败'))
    }
  }

  return {
    ordering,
    orderedItems,
    onItemDragStart,
    enterOrdering,
    onItemDrop,
    saveItemOrder,
  }
}
