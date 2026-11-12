/** 平板/大屏适配（P1 可访问性与大屏）。
 *
 * 手机竖屏 2 列是经验值：卡片宽度 ≈ (360-12*3)/2 ≈ 162dp，缩略图不至于糊。
 * 到了 600dp 以上（平板 / 折叠屏展开 / 横屏），2 列会让每张图宽到 280dp+，
 * 一屏只能看两张，反而低效——这时切 3 列。
 *
 * 阈值取 600dp：Android 官方 large screen 断点（sw600dp），也与 Material 的
 * 「compact < 600 / medium ≥ 600」一致。
 */

/** 大屏断点（dp） */
export const LARGE_SCREEN_BREAKPOINT = 600

/**
 * 素材网格列数。
 *
 * @param width 当前窗口宽度（dp）
 * @param maxColumns 上限（默认 3；候选图/合集封面这类大图也够用）
 */
export function gridColumns(width: number, maxColumns = 3): number {
  if (!Number.isFinite(width) || width <= 0) return 2
  if (width >= 900) return Math.min(maxColumns, 4)
  if (width >= LARGE_SCREEN_BREAKPOINT) return Math.min(maxColumns, 3)
  return 2
}

/** 列表/详情内容的阅读宽度上限（dp）：平板上一行拉太长会很难读 */
export const CONTENT_MAX_WIDTH = 720
