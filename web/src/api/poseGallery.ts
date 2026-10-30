/** 姿势参考图库 API（换姿势用）。
 *
 * 数据由后端代理 ai-pose-gallery-zh（源站静态站无 CORS，前端直连会被拦）。
 * 缩略图/原图地址是**源站直链**：`<img>` 展示不需要 CORS，生成时由后端按需下载。
 */

import apiClient from '@/api/client'

/** 一条姿势（来源：ai-pose-gallery-zh，个人本地使用）。 */
export interface PoseItem {
  /** 唯一 id（形如 `full_std_P001_front`），生成时回传给后端 */
  id: string
  /** 姿势名称（中文） */
  name: string
  /** 体系分组（全身单人・标准系 / 上半身单人 / …） */
  system: string
  /** 动作分类（站姿/坐姿/…） */
  category: string
  /** 观察角度（正面/斜侧/侧面/俯视/仰视） */
  angle: string
  /** 组内编号（如 P001） */
  pose_no: string
  /** 缩略图直链（300×400 JPG，源站） */
  thumb_url: string
  /** 原图直链（900×1200 PNG，源站；后端生成时下载） */
  image_url: string
}

/** 筛选项（带数量，下拉用；只在本模块的元信息结构里出现，不单独 export）。 */
interface PoseFacet {
  name: string
  count: number
}

/** 图库元信息（打开选择器时拉一次）。 */
export interface PoseGalleryMeta {
  total: number
  systems: PoseFacet[]
  categories: PoseFacet[]
  angles: PoseFacet[]
  /** 来源与使用说明（直接展示给用户） */
  note: string
  /** 源站地址（展示/排查用） */
  source_url: string
}

/** 姿势列表查询参数（只给本模块函数用，不单独 export）。 */
interface PoseQuery {
  q?: string
  system?: string
  category?: string
  angle?: string
  page?: number
  size?: number
}

/** 姿势分页结果（只给本模块函数用，不单独 export）。 */
interface PoseList {
  items: PoseItem[]
  total: number
  page: number
  size: number
  note: string
}

/** 图库元信息：总数 + 体系/分类/角度筛选项。 */
export async function fetchPoseGalleryMeta(): Promise<PoseGalleryMeta> {
  const { data } = await apiClient.get<PoseGalleryMeta>('/pose-gallery/meta')
  return data
}

/** 分页查询姿势（服务端筛选）。 */
export async function fetchPoses(query: PoseQuery = {}): Promise<PoseList> {
  const { data } = await apiClient.get<PoseList>('/pose-gallery', { params: query })
  return data
}
