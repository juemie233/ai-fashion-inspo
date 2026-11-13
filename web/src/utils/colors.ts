/** 前端颜色令牌：状态色 / 遮罩色 / 图表主色。
 *
 * 审查（docs/features/前端公共组件使用审查-2026-11-13.md P2-4）结论是**不要**
 * 一次性重写 740 处十六进制颜色；先把最易漂移的三类收敛到单一来源，其余局部
 * 颜色按需迁移。CSS 里通过局部 CSS 变量消费，组件样式仍保持 scoped。
 */

/** 状态标签背景色（StatusTag badge 变体 / statusMeta） */
export const STATUS_BG = {
  success: 'rgba(0, 180, 42, 0.85)',
  warning: 'rgba(255, 125, 0, 0.85)',
  danger: 'rgba(208, 48, 80, 0.85)',
  neutral: 'rgba(160, 160, 160, 0.8)',
  primary: 'rgba(22, 93, 255, 0.8)',
  ai: 'rgba(122, 80, 200, 0.9)',
} as const

/** 缩略图状态遮罩 / 选中框颜色（ThumbCard） */
export const MASK_COLORS = {
  uploading: 'rgba(255, 255, 255, 0.55)',
  done: 'rgba(16, 185, 129, 0.35)',
  failed: 'rgba(239, 68, 68, 0.35)',
  duplicate: 'rgba(250, 204, 21, 0.4)',
  selectedBorder: '#3b82f6',
  selectedFill: 'rgba(59, 130, 246, 0.15)',
} as const

/** 图表主色（ECharts 折线 / 面积） */
export const CHART_COLORS = {
  primary: '#2a78d6',
  grid: '#eee',
  areaOpacity: 0.12,
} as const
