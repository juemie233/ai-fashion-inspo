/** 网格密度单一来源：MasonryGrid 断点与密度文案。 */

import { describe, expect, it } from 'vitest'

import {
  DENSITY_FIXED_COLUMNS,
  GRID_DENSITY_OPTIONS,
  MASONRY_DENSITY_BREAKPOINTS,
  masonryColumnCount,
} from '../gridDensity'

describe('gridDensity', () => {
  it('MasonryGrid 断点按视口宽度取列数', () => {
    expect(masonryColumnCount('compact', 700)).toBe(2)
    expect(masonryColumnCount('compact', 701)).toBe(3)
    expect(masonryColumnCount('compact', 1001)).toBe(4)
    expect(masonryColumnCount('compact', 1401)).toBe(6)

    expect(masonryColumnCount('standard', 700)).toBe(1)
    expect(masonryColumnCount('standard', 1001)).toBe(3)
    expect(masonryColumnCount('standard', 1401)).toBe(4)

    expect(masonryColumnCount('comfortable', 700)).toBe(1)
    expect(masonryColumnCount('comfortable', 1001)).toBe(2)
    expect(masonryColumnCount('comfortable', 1401)).toBe(3)
  })

  it('未知密度回退标准断点', () => {
    expect(masonryColumnCount('unknown' as never, 1401)).toBe(4)
    expect(MASONRY_DENSITY_BREAKPOINTS.standard.at(-1)?.cols).toBe(4)
  })

  it('密度文案与固定列数覆盖三档且顺序稳定', () => {
    expect(GRID_DENSITY_OPTIONS.map((d) => d.value)).toEqual(['compact', 'standard', 'comfortable'])
    expect(GRID_DENSITY_OPTIONS.map((d) => d.label)).toEqual(['紧凑', '标准', '宽松'])
    expect(DENSITY_FIXED_COLUMNS).toEqual({ compact: 6, standard: 4, comfortable: 3 })
  })
})
