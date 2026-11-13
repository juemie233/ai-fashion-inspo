/** 前端网格密度单一来源：类型、文案、MasonryGrid 断点与 DensityImageGrid 列宽/间距。 */

/** 三档密度（紧凑 / 标准 / 宽松） */
export type GridDensity = 'compact' | 'standard' | 'comfortable'

/** 默认密度 */
export const DEFAULT_GRID_DENSITY: GridDensity = 'standard'

/** 密度切换按钮文案（素材库 / 采集结果 / 人物头像等共用） */
export const GRID_DENSITY_OPTIONS: Array<{ label: string; value: GridDensity }> = [
  { label: '紧凑', value: 'compact' },
  { label: '标准', value: 'standard' },
  { label: '宽松', value: 'comfortable' },
]

/** MasonryGrid：视口宽度上限 -> 列数（从窄到宽） */
export interface MasonryBreakpoint {
  max: number
  cols: number
}

export const MASONRY_DENSITY_BREAKPOINTS: Record<GridDensity, MasonryBreakpoint[]> = {
  compact: [
    { max: 700, cols: 2 },
    { max: 1000, cols: 3 },
    { max: 1400, cols: 4 },
    { max: Infinity, cols: 6 },
  ],
  standard: [
    { max: 700, cols: 1 },
    { max: 1000, cols: 2 },
    { max: 1400, cols: 3 },
    { max: Infinity, cols: 4 },
  ],
  comfortable: [
    { max: 700, cols: 1 },
    { max: 1000, cols: 2 },
    { max: 1400, cols: 2 },
    { max: Infinity, cols: 3 },
  ],
}

/** 按视口宽度取 MasonryGrid 列数 */
export function masonryColumnCount(density: GridDensity, viewportWidth: number): number {
  const list = MASONRY_DENSITY_BREAKPOINTS[density] ?? MASONRY_DENSITY_BREAKPOINTS.standard
  return list.find((b) => viewportWidth <= b.max)?.cols ?? 4
}

/** DensityImageGrid：每档自适应最小列宽（px） */
export const DENSITY_GRID_MIN_WIDTH: Record<GridDensity, number> = {
  compact: 100,
  standard: 130,
  comfortable: 170,
}

/** DensityImageGrid：每档网格间距（px） */
export const DENSITY_GRID_GAP: Record<GridDensity, number> = {
  compact: 6,
  standard: 12,
  comfortable: 16,
}

/** 固定列数场景（采集结果预览等）：与 MasonryGrid 宽屏档位一致 */
export const DENSITY_FIXED_COLUMNS: Record<GridDensity, number> = {
  compact: 6,
  standard: 4,
  comfortable: 3,
}
