/** 标签相关的 UI 常量（类别 / 来源中文映射等）。
 *
 * 从 api/tags.ts 迁来，保持 API 层只负责请求；多处复用集中于此。
 */

/** 类别名称的中文映射（类别值为 snake_case，同时是类别下拉选项的来源）
 *
 * design_detail（款式细节）/ material（面料）为新增维度（由原 body_part 拆分）；
 * body_part 保留为遗留类别（仅存无法明确归类的存量）；
 * 旧 PascalCase 类别（Atmosphere/Expression/Leg_Posture）不再进入选项与映射，
 * 历史快照数据的兜底显示由各展示组件自行处理。
 */
export const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  design_detail: '款式细节',
  material: '面料',
  fit: '版型',
  attribute: '属性',
  atmosphere: '氛围',
  expression: '模特表情',
  leg_posture: '腿部姿态',
  outfit: '穿搭大标签',
  free: '自定义',
  body_part: '穿着方式',
}

/** 来源的中文映射 */
export const SOURCE_LABELS: Record<string, string> = {
  seed: '预设',
  ai_generated: 'AI生成',
  manual: '手动',
}
