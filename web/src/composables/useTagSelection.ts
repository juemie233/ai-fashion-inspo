/** 标签多选状态：统一用 Set 存储，同时暴露数组形式供 Arco 表格 row-selection 使用。
 *
 * 各页面（标签管理 / 健康度面板等）分别调用本函数获得独立的选中状态，
 * 替代此前散落的 Set + number[] 两套写法。
 */

import { computed, ref } from 'vue'

export function useTagSelection() {
  /** 内部用 Set 保证 O(1) 去重；整体替换以触发 Vue 响应式更新 */
  const selectedIds = ref<Set<number>>(new Set())

  /** 数组形式：供 a-table 的 :row-selection="{ selectedRowKeys }" 使用 */
  const selectedKeys = computed(() => Array.from(selectedIds.value))

  const count = computed(() => selectedIds.value.size)
  const hasAny = computed(() => selectedIds.value.size > 0)

  function has(id: number): boolean {
    return selectedIds.value.has(id)
  }

  function toggle(id: number) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }

  function add(id: number) {
    if (selectedIds.value.has(id)) return
    const next = new Set(selectedIds.value)
    next.add(id)
    selectedIds.value = next
  }

  function addMany(ids: Iterable<number>) {
    const next = new Set(selectedIds.value)
    let changed = false
    for (const id of ids) {
      if (!next.has(id)) {
        next.add(id)
        changed = true
      }
    }
    if (changed) selectedIds.value = next
  }

  function remove(id: number) {
    if (!selectedIds.value.has(id)) return
    const next = new Set(selectedIds.value)
    next.delete(id)
    selectedIds.value = next
  }

  function clear() {
    if (selectedIds.value.size === 0) return
    selectedIds.value = new Set()
  }

  /** 选中一组（替换式，用于「全选本组」） */
  function setAll(ids: Iterable<number>) {
    selectedIds.value = new Set(ids)
  }

  /** Arco selection-change 回调入参为 (string|number)[]，统一收敛 */
  function setFromKeys(keys: Array<string | number>) {
    selectedIds.value = new Set(keys.map(Number))
  }

  return {
    selectedIds,
    selectedKeys,
    count,
    hasAny,
    has,
    toggle,
    add,
    addMany,
    remove,
    clear,
    setAll,
    setFromKeys,
  }
}
