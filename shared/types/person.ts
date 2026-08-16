/** 共享类型定义：人物（模特 / 博主）相关。Web 和 Mobile 端共用。 */

/** 人物内容类型：职业模特写真 / 博主穿搭（UI 区分呈现的核心维度） */
export const PERSON_TYPES = ['model', 'blogger'] as const
export type PersonType = typeof PERSON_TYPES[number]

/** 内容类型中文名称 */
export const PERSON_TYPE_LABELS: Record<PersonType, string> = {
  model: '职业模特',
  blogger: '穿搭博主',
}

/** 人物平台 */
export const PERSON_PLATFORMS = ['xiaohongshu', 'douyin', 'other'] as const
export type PersonPlatform = typeof PERSON_PLATFORMS[number]

/** 人物平台中文名称 */
export const PERSON_PLATFORM_LABELS: Record<PersonPlatform, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  other: '其他',
}

/** 人物对象 */
export interface Person {
  id: number
  name: string
  person_type: PersonType
  platform: PersonPlatform
  platform_user_id?: string | null
  profile_url?: string | null
  avatar_path?: string | null
  bio?: string | null
  source?: string
  created_at?: string | null
  updated_at?: string | null
  inspiration_count?: number
}

/** 人物简要信息（素材详情中关联人物展示用） */
export interface PersonBrief {
  id: number
  name: string
  person_type: PersonType
  platform: PersonPlatform
  avatar_path?: string | null
}

/** 人物风格画像：聚合其素材标签的频次 / 类别分布 / 时间趋势 */
export interface PersonStyleProfile {
  top_tags: Array<{ tag_id: number; name: string; category: string; count: number }>
  by_category: Record<string, number>
  trend: Array<{ bucket: string; count: number }>
}

/** 人物详情（含风格画像） */
export interface PersonDetail extends Person {
  style_profile: PersonStyleProfile
}

/** 人物分页列表 */
export interface PersonListOut {
  items: Person[]
  total: number
  page: number
  size: number
}
