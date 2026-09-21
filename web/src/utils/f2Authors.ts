/**
 * 「查看已登记博主」清单的排序工具（纯函数，便于单测）。
 *
 * 为什么方向要自己处理：Arco 表头点击传了**自定义 `sorter` 时不会自己套 `direction`**
 * ——只有不传 sorter 的默认比较路径才会按 `direction` 取反（见 web-vue 的 `sortedData`
 * 实现）。所以 `descend` / `ascend` 必须在 sorter 内部体现，否则点表头方向不变。
 */

/** 可排序的数值列：总作品数 / 已入库素材数 */
export type AuthorSortField = 'aweme_count' | 'materials'

/** 表格行（Arco 传给 sorter 的是行对象本身，这里只按字段取值） */
type SortableRow = Record<string, unknown>

/**
 * 生成数值列的表头排序器。
 *
 * 口径：空值（未登记 / 无数据，表里显示「—」）当 0 参与排序，避免出现
 * 「有的行算不出来」的不确定顺序；同值时返回 0，由表格保持稳定顺序。
 *
 * @param field 列字段名
 * @returns Arco `TableSortable.sorter` 所需的比较函数
 */
export function authorColumnSorter(field: AuthorSortField) {
  return (a: SortableRow, b: SortableRow, extra: { direction: 'ascend' | 'descend' }): number => {
    const diff = Number(a[field] ?? 0) - Number(b[field] ?? 0)
    if (diff === 0) {
      return 0 // 同值（含空值当 0）：显式返回 +0，别返回 -0
    }
    return extra.direction === 'descend' ? -diff : diff
  }
}
