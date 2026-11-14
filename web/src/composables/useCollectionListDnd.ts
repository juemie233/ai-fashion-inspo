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

  /** 按当前顺序生成完整 id 列表（一级后面紧跟它的二级，保持父子不拆散） */
  function fullOrderIds(): number[] {
    const byParent = new Map<number | null, CollectionOut[]>()
    for (const item of collections.value) {
      const list = byParent.get(item.parent_id) ?? []
      list.push(item)
      byParent.set(item.parent_id, list)
    }
    const roots = byParent.get(null) ?? []
    return roots.flatMap((root) => [
      root.id,
      ...(byParent.get(root.id) ?? []).map((child) => child.id),
    ])
  }

  async function persistOrder(ids: number[]) {
    const byId = new Map(collections.value.map((item) => [item.id, item]))
    collections.value = ids
      .map((id) => byId.get(id))
      .filter((item): item is CollectionOut => !!item)
    try {
      await reorderCollections(ids)
      await loadCollections()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '排序保存失败'))
      loadCollections()
    }
  }

  function onCollectionDragStart(id: number) {
    draggingCollectionId = id
  }

  async function onCollectionDrop(targetId: number) {
    const from = draggingCollectionId
    draggingCollectionId = null
    dragOverId.value = null
    if (from === null || from === targetId) return
    const ids = fullOrderIds()
    const fromIdx = ids.indexOf(from)
    const toIdx = ids.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    ids.splice(toIdx, 0, ids.splice(fromIdx, 1)[0])
    await persistOrder(ids)
  }

  /** 触屏替代：把一级合集整体上移/下移一位（二级收藏夹跟着走） */
  async function onCollectionMove(id: number, direction: 'up' | 'down') {
    const roots = collections.value.filter((item) => item.parent_id === null)
    const idx = roots.findIndex((root) => root.id === id)
    if (idx < 0) return
    const targetIdx = direction === 'up' ? idx - 1 : idx + 1
    if (targetIdx < 0 || targetIdx >= roots.length) return
    const rootIds = roots.map((root) => root.id)
    ;[rootIds[idx], rootIds[targetIdx]] = [rootIds[targetIdx], rootIds[idx]]
    const byParent = new Map<number, number[]>()
    for (const item of collections.value) {
      if (item.parent_id == null) continue
      const list = byParent.get(item.parent_id) ?? []
      list.push(item.id)
      byParent.set(item.parent_id, list)
    }
    await persistOrder(rootIds.flatMap((rootId) => [rootId, ...(byParent.get(rootId) ?? [])]))
  }

  return { dragOverId, onCollectionDragStart, onCollectionDrop, onCollectionMove }
}
