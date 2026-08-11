/** 共享类型定义：灵感素材相关。Web 和 Mobile 端共用。 */

/** 灵感的来源类型 */
export type SourceType = 'xiaohongshu' | 'douyin' | 'manual_upload' | 'browser_extension'

/** 媒体类型 */
export type MediaType = 'image' | 'video_frame' | 'video'

/** AI 分析状态 */
export type AnalysisStatus = 'none' | 'analyzing' | 'done' | 'error'

/** 标签类别 */
export const TAG_CATEGORIES = [
  'style', 'item_type', 'color', 'body_part',
  'fit', 'occasion', 'season', 'attribute', 'free',
] as const
export type TagCategory = typeof TAG_CATEGORIES[number]

/** 标签类别的中文名称 */
export const TAG_CATEGORY_LABELS: Record<TagCategory, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  occasion: '场合',
  season: '季节',
  attribute: '属性',
  free: '自定义',
}
