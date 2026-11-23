/**
 * 标签多选状态（`useTagSelection`）的测试。
 *
 * 这是标签管理页批量操作的唯一选中源，语义要点：
 * - 内部 Set 每次变更**整体替换**（否则 Vue 对 Set 的增删不触发更新）；
 * - 空操作（重复 add / remove 不存在 / clear 空集）不替换引用，避免无谓重渲染；
 * - `selectedKeys` 是 Arco 表格 row-selection 需要的数组形式，且要随选中实时更新。
 */

import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import { useTagSelection } from '../useTagSelection'

describe('useTagSelection', () => {
  let selection: ReturnType<typeof useTagSelection>

  beforeEach(() => {
    selection = useTagSelection()
  })

  it('初始为空：count=0、hasAny=false、selectedKeys=[]', () => {
    expect(selection.selectedIds.value.size).toBe(0)
    expect(selection.selectedKeys.value).toEqual([])
    expect(selection.count.value).toBe(0)
    expect(selection.hasAny.value).toBe(false)
  })

  it('toggle 增删都替换成新 Set，并同步 computed', async () => {
    const before = selection.selectedIds.value

    selection.toggle(3)

    expect(selection.has(3)).toBe(true)
    expect(selection.selectedIds.value).not.toBe(before)
    expect(selection.selectedKeys.value).toEqual([3])
    expect(selection.count.value).toBe(1)
    expect(selection.hasAny.value).toBe(true)

    const afterAdd = selection.selectedIds.value
    selection.toggle(3)

    expect(selection.has(3)).toBe(false)
    expect(selection.selectedIds.value).not.toBe(afterAdd)
    expect(selection.selectedKeys.value).toEqual([])
    await nextTick()
    expect(selection.count.value).toBe(0)
  })

  it('add：重复添加是空操作（引用不变）；新增才替换', () => {
    selection.add(1)
    const ref = selection.selectedIds.value

    selection.add(1)
    expect(selection.selectedIds.value).toBe(ref)

    selection.add(2)
    expect(selection.selectedIds.value).not.toBe(ref)
    expect(selection.selectedKeys.value).toEqual([1, 2])
  })

  it('addMany：批量去重追加；全部已存在时引用不变', () => {
    selection.addMany([1, 2, 2])
    expect(selection.selectedKeys.value).toEqual([1, 2])

    const ref = selection.selectedIds.value
    selection.addMany([2, 1])
    expect(selection.selectedIds.value).toBe(ref)

    selection.addMany([1, 3])
    expect(selection.selectedIds.value).not.toBe(ref)
    expect(selection.selectedKeys.value).toEqual([1, 2, 3])
  })

  it('remove：不存在时不替换引用；存在时删除', () => {
    selection.setAll([1, 2])
    const ref = selection.selectedIds.value

    selection.remove(9)
    expect(selection.selectedIds.value).toBe(ref)

    selection.remove(1)
    expect(selection.selectedIds.value).not.toBe(ref)
    expect(selection.selectedKeys.value).toEqual([2])
  })

  it('clear：空集不替换引用；非空清空', () => {
    const emptyRef = selection.selectedIds.value
    selection.clear()
    expect(selection.selectedIds.value).toBe(emptyRef)

    selection.setAll([1, 2, 3])
    const ref = selection.selectedIds.value
    selection.clear()
    expect(selection.selectedIds.value).not.toBe(ref)
    expect(selection.selectedIds.value.size).toBe(0)
  })

  it('setAll 是替换式（用于「全选本组」），不与旧选中合并', () => {
    selection.setAll([1, 2])
    selection.setAll([3])

    expect(selection.selectedKeys.value).toEqual([3])
  })

  it('setFromKeys 把 Arco 的 (string|number)[] 收敛成 number Set', () => {
    selection.setFromKeys(['4', 5, '6'])

    expect(selection.selectedKeys.value).toEqual([4, 5, 6])
    expect(selection.has(5)).toBe(true)
    expect(selection.has(4)).toBe(true)
  })

  it('selectedKeys 每次返回新数组但内容与 Set 一致（供表格受控）', () => {
    selection.setAll([2, 1])

    expect(selection.selectedKeys.value).toEqual([2, 1])
    selection.selectedKeys.value.push(99)
    // 外部改数组不能污染内部 Set
    expect(selection.selectedIds.value.size).toBe(2)
  })
})
