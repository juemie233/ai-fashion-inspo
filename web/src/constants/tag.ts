/** 标签相关的 UI 常量（类别 / 来源中文映射等）。
 *
 * 从 api/tags.ts 迁来，保持 API 层只负责请求；多处复用集中于此。
 */

/** 类别名称的中文映射 */
export const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  attribute: '属性',
  free: '自定义',
  outfit: '穿搭大标签',
  Atmosphere: '氛围',
  Expression: '模特表情',
  Leg_Posture: '腿部姿态',
}

/** 来源的中文映射 */
export const SOURCE_LABELS: Record<string, string> = {
  seed: '预设',
  ai_generated: 'AI生成',
  manual: '手动',
}
