/** 批量选择用的集合操作（纯函数，两个 composable 共用，避免各写一份同样的 3 行）。
 *
 * 约定：**返回新 Set**、不就地修改——composable 里的 `selectedIds` 是 ref，
 * 只有替换整个 Set 才会触发 Vue 的依赖更新（原地 mutate 不触发）。
 */

/** 切换单个 id 的选中态 */
export function toggleInSet<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

/** 全选 / 取消全选：全部已选则清空，否则全选（`all` 由调用方给出可操作范围） */
export function toggleAllInSet<T>(all: readonly T[], selected: ReadonlySet<T>): Set<T> {
  const allSelected = all.length > 0 && all.every((id) => selected.has(id))
  return allSelected ? new Set() : new Set(all)
}
