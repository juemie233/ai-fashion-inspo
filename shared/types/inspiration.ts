/** 共享类型定义：灵感素材相关。Web 和 Mobile 端共用。 */

/** 灵感的来源类型。
 *
 * 与后端 `inspirations.source_type` 的实际取值保持一致（2026-10 核对：
 * `generated` 由出图入库写入，`url_import` 由链接导入写入，两者原先漏在这份定义里，
 * mobile 的本地映射表反而比共享类型更全 —— 收敛类型时把漂移补回来）。
 */
export type SourceType =
  | 'xiaohongshu'
  | 'douyin'
  | 'manual_upload'
  | 'scraper'
  | 'browser_extension'
  | 'batch_import'
  | 'url_import'
  | 'generated'

/** 媒体类型 */
export type MediaType = 'image' | 'video_frame' | 'video'

/** AI 分析状态 */
export type AnalysisStatus = 'none' | 'analyzing' | 'done' | 'error'

/** 标签类别。
 *
 * 与后端实际写入路径保持一致（2026-11-16 按 `backend/app/services/ai_tag_saver.py`
 * 的 category_map、`tag_crud.py` 的 SEED_TAGS、inspirations 手动大标签路径，
 * 以及现网 tags 表的真实分布核对）：删除库里从未出现过的 `season`，
 * 补回 design_detail / material / atmosphere / expression / leg_posture / outfit。
 * 新增类别前必须先确认后端有写入路径，否则前端选项永远筛不出数据。
 */
export const TAG_CATEGORIES = [
  'style',
  'item_type',
  'color',
  'body_part',
  'fit',
  'attribute',
  'atmosphere',
  'expression',
  'leg_posture',
  'design_detail',
  'material',
  'outfit',
  'free',
] as const
export type TagCategory = (typeof TAG_CATEGORIES)[number]

/** 标签类别的中文名称（Web / Mobile 共用真源，两端只展开不重写） */
export const TAG_CATEGORY_LABELS: Record<TagCategory, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  attribute: '属性',
  atmosphere: '氛围',
  expression: '模特表情',
  leg_posture: '腿部姿态',
  design_detail: '款式细节',
  material: '面料',
  outfit: '穿搭大标签',
  free: '自定义',
}
