/** f2「已登记博主」清单排序器单测：方向、空值当 0、同值稳定。 */

import { describe, expect, it } from 'vitest'
import { authorColumnSorter } from '@/utils/f2Authors'

describe('authorColumnSorter', () => {
  it('descend：数值大的排前面（总作品数）', () => {
    const sorter = authorColumnSorter('aweme_count')
    const rows = [{ aweme_count: 3 }, { aweme_count: 171 }, { aweme_count: 20 }]
    const sorted = [...rows].sort((a, b) => sorter(a, b, { direction: 'descend' }))
    expect(sorted.map((r) => r.aweme_count)).toEqual([171, 20, 3])
  })

  it('ascend：数值小的排前面（已入库素材数）', () => {
    const sorter = authorColumnSorter('materials')
    const rows = [{ materials: 512 }, { materials: 0 }, { materials: 88 }]
    const sorted = [...rows].sort((a, b) => sorter(a, b, { direction: 'ascend' }))
    expect(sorted.map((r) => r.materials)).toEqual([0, 88, 512])
  })

  it('空值当 0：未登记的博主与真正 0 一起排到最后（降序时）', () => {
    const sorter = authorColumnSorter('aweme_count')
    const rows = [{ aweme_count: 5 }, { aweme_count: null }, { aweme_count: 9 }]
    const sorted = [...rows].sort((a, b) => sorter(a, b, { direction: 'descend' }))
    expect(sorted.map((r) => r.aweme_count)).toEqual([9, 5, null])
  })

  it('字段缺失同样当 0，不抛错', () => {
    const sorter = authorColumnSorter('materials')
    expect(sorter({}, { materials: 7 }, { direction: 'descend' })).toBeGreaterThan(0)
    expect(sorter({}, { materials: 7 }, { direction: 'ascend' })).toBeLessThan(0)
  })

  it('同值返回 0（表格据此保持稳定顺序）', () => {
    const sorter = authorColumnSorter('aweme_count')
    expect(sorter({ aweme_count: 42 }, { aweme_count: 42 }, { direction: 'descend' })).toBe(0)
    expect(sorter({ aweme_count: 42 }, { aweme_count: 42 }, { direction: 'ascend' })).toBe(0)
  })

  it('两个方向结果互为相反数（点击表头能在逆序/正序间切换）', () => {
    const sorter = authorColumnSorter('materials')
    const a = { materials: 3 }
    const b = { materials: 10 }
    expect(sorter(a, b, { direction: 'descend' })).toBe(-sorter(a, b, { direction: 'ascend' }))
  })
})
