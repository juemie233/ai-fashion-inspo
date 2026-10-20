/** 合集列表的拖拽排序（从 CollectionsView.vue 抽出，F 批）。
 *
 * 为什么抽：视图已过 800 行，而「拖拽 → 乐观更新 → 持久化 → 失败回滚」是一段
 * 自成体系的交互逻辑，与视图的编排职责无关；抽出来后视图只保留 `v-model` 接线。
 *
 * 拖拽中的 ID 用普通 `let` 而不是 `ref`：它只在 dragstart → drop 之间传递，
 * 不需要响应式；模板里也**必须**通过函数赋值（见 `onCollectionDragStart` 的注释，
 * 直接写 `draggingCollectionId = id` 会让 IDE 的 Vue 语言服务把绑定推断成 null）。
 */

import { ref, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import { reorderCollections, type CollectionOut } from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'

interface CollectionListDndDeps {
  /** 当前合集列表（原地排序，模板直接渲染它） */
  collections: Ref<CollectionOut[]>
  /** 重新拉取列表（失败回滚 / 排序后与后端对齐） */
  loadCollections: () => Promise<void>
}

export function useCollectionListDnd(opts: CollectionListDndDeps) {
  const { collections, loadCollections } = opts

  const dragOverId = ref<number | null>(null)
  let draggingCollectionId: number | null = null

  function onCollectionDragStart(id: number) {
    draggingCollectionId = id
  }

  async function onCollectionDrop(targetId: number) {
    const from = draggingCollectionId
    draggingCollectionId = null
    dragOverId.value = null
    if (from === null || from === targetId) return
    const ids = collections.value.map((c) => c.id)
    const fromIdx = ids.indexOf(from)
    const toIdx = ids.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    ids.splice(toIdx, 0, ids.splice(fromIdx, 1)[0])
    // 乐观更新 + 后端持久化
    collections.value.sort((x, y) => ids.indexOf(x.id) - ids.indexOf(y.id))
    try {
      await reorderCollections(ids)
      await loadCollections()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '排序保存失败'))
      loadCollections()
    }
  }

  return { dragOverId, onCollectionDragStart, onCollectionDrop }
}
