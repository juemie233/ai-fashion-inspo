/** 共享类型定义：人物（穿搭博主 / 职业模特）相关。Web 和 Mobile 端共用。
 *
 * 博主与模特已物理拆分为两张独立表（bloggers / models），API 也拆分为
 * /api/bloggers 与 /api/models；此处保留统一基类型 ``Person`` 便于共用
 * 字段，实际使用以 ``Blogger`` / ``Model`` 区分。
 */

/** 人物平台 */
export const PERSON_PLATFORMS = ["xiaohongshu", "douyin", "other"] as const;
export type PersonPlatform = (typeof PERSON_PLATFORMS)[number];

/** 人物平台中文名称 */
export const PERSON_PLATFORM_LABELS: Record<PersonPlatform, string> = {
  xiaohongshu: "小红书",
  douyin: "抖音",
  other: "其他",
};

/** 人物内容类型：blogger（穿搭博主）/ model（职业模特） */
export const PERSON_TYPES = ["model", "blogger"] as const;
export type PersonType = (typeof PERSON_TYPES)[number];

/** 内容类型中文名称（素材关联徽标 / 历史字段展示用） */
export const PERSON_TYPE_LABELS: Record<PersonType, string> = {
  model: "职业模特",
  blogger: "穿搭博主",
};

/** 人物统一基类型（博主/模特共用字段；person_type 仅历史/素材关联场景使用） */
export interface Person {
  id: number;
  name: string;
  person_type?: "blogger" | "model";
  platform: PersonPlatform;
  platform_user_id?: string | null;
  xhs_id?: string | null;
  ip_location?: string | null;
  /** 是否已注册人脸特征（face_embedding 非空）：人脸检测只匹配库内人物 */
  face_registered?: boolean;
  profile_url?: string | null;
  avatar_path?: string | null;
  /** 人脸缩略图相对路径（博主专属：从已匹配素材的人脸检测框裁剪；模特无此字段） */
  face_thumb_path?: string | null;
  bio?: string | null;
  source?: string;
  created_at?: string | null;
  updated_at?: string | null;
  inspiration_count?: number;
  /** 人物组 ID（方案 B，仅博主）：null/undefined = 独立账号；非空 = 与组内其它账号为同一人 */
  person_group_id?: number | null;
  /** 折叠视图：组内其余账号（展开显示用）；独立账号/平铺视图为空数组 */
  group_members?: Person[];
  /** 组内平台去重列表（多平台徽标，如 ["douyin", "xiaohongshu"]） */
  group_platforms?: PersonPlatform[];
}

/** 穿搭博主（对应 /api/bloggers，拥有小红书号/IP 属地、CSV 导入等能力） */
export interface Blogger extends Person {
  person_type?: "blogger";
}

/** 职业模特（对应 /api/models，拥有写真照片组能力） */
export interface Model extends Person {
  person_type?: "model";
}

/** CSV 导入单行失败明细 */
export interface PersonImportError {
  row: number;
  nickname?: string | null;
  reason: string;
}

/** CSV 导入结果统计 */
export interface PersonImportResult {
  imported: number;
  updated: number;
  skipped: number;
  failed: number;
  errors: PersonImportError[];
}

/** 人物简要信息（素材详情中关联展示用） */
export interface PersonBrief {
  id: number;
  name: string;
  platform: PersonPlatform;
  avatar_path?: string | null;
}

/** 人物风格画像：聚合其素材标签的频次 / 类别分布 / 时间趋势 */
export interface PersonStyleProfile {
  top_tags: Array<{
    tag_id: number;
    name: string;
    category: string;
    count: number;
  }>;
  by_category: Record<string, number>;
  trend: Array<{ bucket: string; count: number }>;
}

/** 人物详情（含风格画像） */
export interface PersonDetail extends Person {
  style_profile: PersonStyleProfile;
}

/** 人物分页列表 */
export interface PersonListOut<T = Person> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

/** 模特照片：照片组内的一张写真照片 */
export interface ModelPhoto {
  id: number;
  set_id: number;
  file_path: string;
  thumbnail_path?: string | null;
  sort_order?: number;
  created_at?: string | null;
}

/** 模特照片组：一次从文件夹导入的一组写真 */
export interface ModelPhotoSet {
  id: number;
  model_id: number;
  name: string;
  photo_count: number;
  cover_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** 模特照片组详情（含分页照片列表） */
export interface ModelPhotoSetDetail extends ModelPhotoSet {
  photos: ModelPhoto[];
  total: number;
  page: number;
  size: number;
}

/** 模特照片组分页列表 */
export interface ModelPhotoSetListOut {
  items: ModelPhotoSet[];
  total: number;
  page: number;
  size: number;
}
