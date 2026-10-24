/** 人物相关 API 调用（穿搭博主 / 职业模特已拆分两表，工厂生成两组端点）。 */

import { createPersonApi } from './personBase'
import apiClient from './client'
// 只导入本文件**代码里**用到的类型；其余类型由下方 `export type { ... } from` 直接转发
import type { Blogger, PersonPlatform } from '@shared/types/person'

export type {
  Blogger,
  Model,
  PersonBrief,
  PersonDetail,
  PersonImportResult,
  PersonListOut,
  PersonPlatform,
  ModelPhoto,
  ModelPhotoSet,
  ModelPhotoSetDetail,
  ModelPhotoSetListOut,
} from '@shared/types/person'

/** 人物表单载荷（创建/更新共用，更新时字段均可选） */
export interface PersonForm {
  name?: string
  platform?: PersonPlatform
  platform_user_id?: string | null
  xhs_id?: string | null
  ip_location?: string | null
  profile_url?: string | null
  avatar_path?: string | null
  bio?: string | null
  /** 按人人脸匹配阈值（0~1）：显式传 null 清除，回退全局配置 */
  face_match_threshold?: number | null
}

/** 人物素材项（与标签详情素材结构一致） */
export interface PersonInspiration {
  inspiration_id: string
  file_path: string
  thumbnail_path: string | null
  media_type: string
  confidence: number
  created_at: string | null
}

/** 人物数量统计（总数 + 平台分布） */
export interface PersonStats {
  total: number
  items: Array<{ platform: string; count: number }>
}

/** 人物素材列表响应 */
export interface PersonInspirationsOut {
  person: { id: number; name: string; platform: string }
  items: PersonInspiration[]
  total: number
  page: number
  size: number
}

/** IP 属地统计响应（人物管理页地域分布） */
export interface PersonIpStats {
  total: number
  items: Array<{ ip_location: string; count: number }>
}

/**
 * 按类型生成人物 API（博主 / 模特共用 CRUD，端点路径按 kind 区分）。
 * 照片组端点仅由模特 API 暴露（博主无写真照片组能力）。
 */

/** 人物头像设置结果（POST/DELETE /api/bloggers/{id}/avatar） */
export interface AvatarSetResult {
  /** 头像相对路径（相对 storage_root）；清除后为 null */
  avatar_path: string | null
  /** 头像是从照片里裁出的人脸特写（false = 照片里没检出人脸，用了整张照片） */
  face_cropped: boolean
  /** 后端给用户看的提示文案（已提取人脸 / 未检出人脸回退整张 / 已清除） */
  message: string
  blogger?: Blogger
}

/** 「解析抖音主页」结果：ok=false 时 reason 是给用户看的原因（提示后让 TA 手工补链接） */
export interface DouyinProfileResolveResult {
  ok: boolean
  blogger_id: number
  name: string
  profile_url: string
  platform_user_id: string
  /** f2 顺手带回来的 IP 属地（只补空缺；空串表示没拿到） */
  ip_location: string
  /** 解析路径：local（本地互推）/ f2_nickname（f2 用户库昵称命中）/ f2_aweme（按作品反查） */
  source: string
  reason: string
}

/** 批量操作的逐条明细（跳过/被挡下的原因） */
export interface PersonBatchSkip {
  id: number
  name: string
  reason: string
}

/** 批量纳入追踪结果（仅本模块使用） */
interface BatchPromoteResult {
  promoted: number
  skipped: PersonBatchSkip[]
}

/** 批量删除结果（有素材关联的会进 blocked 并给出原因；仅本模块使用） */
interface BatchDeleteResult {
  deleted: number
  blocked: PersonBatchSkip[]
}

/** 头像覆盖率：总数 / 已有头像 / 现在能补（有素材且没头像） */
export interface AvatarCoverage {
  total: number
  with_avatar: number
  fillable: number
}

/** 同名建议里的一条账号（仅本模块内组合用，不对外导出） */
interface DuplicateNameItem {
  id: number
  name: string
  platform: string
  source?: string | null
  person_group_id?: number | null
  profile_url?: string | null
  created_at?: string | null
  inspiration_count: number
}

/** 同名建议（归一化昵称相同的一组账号） */
export interface DuplicateNameGroup {
  key: string
  items: DuplicateNameItem[]
}

/** 穿搭博主 API（/api/bloggers）：含人脸特征注册与人物组绑定 */
export const bloggersApi = {
  ...createPersonApi('bloggers'),

  /**
   * 解析抖音博主的主页标识（sec_user_id + 主页链接）。
   *
   * 用在「下载所有作品」之前：素材来自抖音「我的喜欢 / 我的收藏」时只带作者昵称，补建的
   * 博主没有主页链接。后端按「本地互推 → f2 用户库昵称唯一命中（离线）→ 拿素材的真实作品
   * ID 跑一次 f2 反查作者（联网）」逐级解析；联网那一步实测 20~40 秒。
   *
   * 超时给到 200 秒：后端最多试 2 个作品、每个 f2 运行上限 90 秒（最坏 180 秒）。真超时也
   * 不必慌——第一次的解析结果通常已经写库，再点一次会走「本地互推」秒回。
   */
  async resolveDouyinProfile(bloggerId: number): Promise<DouyinProfileResolveResult> {
    const { data } = await apiClient.post<DouyinProfileResolveResult>(
      `/bloggers/${bloggerId}/resolve-douyin-profile`,
      undefined,
      { timeout: 200000 },
    )
    return data
  },

  // ── 人物组（方案 B）：同一现实人物跨平台账号绑定 ──

  /** 把两个博主绑定为同一人（新建组），或把博主并入已有组 */
  async linkGroup(body: {
    blogger_id: number
    target_blogger_id?: number
    group_id?: number
  }): Promise<PersonGroupInfo> {
    const { data } = await apiClient.post<PersonGroupInfo>('/bloggers/groups/link', body)
    return data
  },

  /** 把博主移出人物组（变独立账号；组内剩 1 个时自动删组） */
  async unlinkGroup(bloggerId: number): Promise<{
    blogger_id: number
    removed_group_id: number | null
  }> {
    const { data } = await apiClient.post('/bloggers/groups/unlink', { blogger_id: bloggerId })
    return data
  },

  /** 手动指定组内主账号（列表折叠展示位） */
  async setPrimaryGroup(groupId: number, bloggerId: number): Promise<PersonGroupInfo> {
    const { data } = await apiClient.post<PersonGroupInfo>(
      `/bloggers/groups/${groupId}/set-primary`,
      { blogger_id: bloggerId },
    )
    return data
  },

  /**
   * 把「自动登记」的博主纳入追踪（source 改回 manual）：
   * 之后「一键获取素材（博主主页）」会增量下载 TA 的主页新作品。
   */
  async promote(bloggerId: number): Promise<Blogger> {
    const { data } = await apiClient.post<Blogger>(`/bloggers/${bloggerId}/promote`)
    return data
  },

  /** 查询人物组完整信息（组内各账号 + 主账号） */
  async fetchGroupInfo(groupId: number): Promise<PersonGroupInfo> {
    const { data } = await apiClient.get<PersonGroupInfo>(`/bloggers/groups/${groupId}`)
    return data
  },

  // ── 批量操作（人物管理页多选）──

  /** 批量把「自动登记」的博主纳入追踪（非自动登记的进 skipped 并说明原因） */
  async batchPromote(ids: number[]): Promise<BatchPromoteResult> {
    const { data } = await apiClient.post<BatchPromoteResult>('/bloggers/batch/promote', {
      ids,
    })
    return data
  },

  /** 批量删除博主（有素材关联的会被挡下，进 blocked 并给出原因） */
  async batchDelete(ids: number[]): Promise<BatchDeleteResult> {
    const { data } = await apiClient.post<BatchDeleteResult>('/bloggers/batch/delete', { ids })
    return data
  },

  /** 头像覆盖率（顶部统计卡片：已设头像 N/总数，还能补 M） */
  async avatarCoverage(): Promise<AvatarCoverage> {
    const { data } = await apiClient.get<AvatarCoverage>('/bloggers/avatars/coverage')
    return data
  },

  /** 批量清除头像（ids 缺省 = 全部；文件由后端在提交后删除） */
  async clearAvatars(ids?: number[]): Promise<{ cleared: number }> {
    const { data } = await apiClient.post<{ cleared: number }>('/bloggers/avatars/clear', {
      ids,
    })
    return data
  },

  /** 同名建议：归一化昵称相同的账号分组（只给建议，合并/删除由用户决定） */
  async duplicateNames(): Promise<{ groups: DuplicateNameGroup[]; total: number }> {
    const { data } = await apiClient.get<{ groups: DuplicateNameGroup[]; total: number }>(
      '/bloggers/duplicate-names',
    )
    return data
  },

  /** 注册/重新注册博主人脸：上传照片与/或已关联素材（合计 1~5 张，重复注册覆盖） */
  async registerFace(
    id: number,
    files: File[],
    inspirationIds?: string[],
  ): Promise<BloggerFaceRegisterResult> {
    const formData = new FormData()
    files.forEach((f) => formData.append('files', f))
    if (inspirationIds && inspirationIds.length > 0) {
      formData.append('inspiration_ids', JSON.stringify(inspirationIds))
    }
    // 显式 multipart：全局默认 application/json 会让 axios 把 FormData 序列化成 JSON（后端 422）
    const { data } = await apiClient.post<BloggerFaceRegisterResult>(
      `/bloggers/${id}/face`,
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' },
      },
    )
    return data
  },

  /** 查询博主人脸注册状态 */
  async fetchFaceStatus(id: number): Promise<BloggerFaceStatus> {
    const { data } = await apiClient.get<BloggerFaceStatus>(`/bloggers/${id}/face`)
    return data
  },

  /**
   * 设置博主头像（覆盖旧的）：从 TA 的素材里选一张，或本地上传一张照片（二选一）。
   *
   * 后端会**提取人像**：送人脸识别子服务取照片里最大的一张人脸，按检测框外扩裁成
   * 256×256 正方形头像；照片里没检出人脸（或人脸子服务不可用）时回退整张照片
   * （长边 ≤512）。返回 face_cropped 说明走的是哪条路。
   */
  async setAvatar(
    id: number,
    payload: { inspirationId?: string; file?: File },
  ): Promise<AvatarSetResult> {
    const formData = new FormData()
    if (payload.file) formData.append('file', payload.file)
    if (payload.inspirationId) formData.append('inspiration_id', payload.inspirationId)
    // 显式 multipart：全局默认 application/json 会让 axios 把 FormData 序列化成 JSON（后端 422）
    const { data } = await apiClient.post<AvatarSetResult>(`/bloggers/${id}/avatar`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  /** 清除头像（avatar_path 置空、回退首字占位），同时删除头像文件 */
  async clearAvatar(id: number): Promise<AvatarSetResult> {
    const { data } = await apiClient.delete<AvatarSetResult>(`/bloggers/${id}/avatar`)
    return data
  },

  /** 注销博主人脸：删除人脸特征、回退所有人脸匹配记录、解除全部素材归属（博主账号保留） */
  async unregisterFace(id: number): Promise<BloggerUnbindResult> {
    const { data } = await apiClient.delete<BloggerUnbindResult>(`/bloggers/${id}/face`)
    return data
  },

  /** 解除博主与素材的归属（保留人脸特征）。
   *  inspirationIds 传空/不传 = 清空该博主全部素材归属；传数组 = 仅解绑指定素材。
   *  同时把这些素材里识别为该博主的人脸记录回退为未匹配（含已确认锁定）。 */
  async unbindInspirations(id: number, inspirationIds?: string[]): Promise<BloggerUnbindResult> {
    const { data } = await apiClient.post<BloggerUnbindResult>(
      `/bloggers/${id}/unbind-inspirations`,
      { inspiration_ids: inspirationIds ?? null },
    )
    return data
  },

  /** 上传选了博主后触发后台人脸绑定：检测素材人脸并把「主脸」直接绑定该博主
   *  （异步执行，不阻塞上传；合照仅绑主脸，无人脸不绑，失败不致命）。 */
  async bindFace(id: number, inspirationId: string): Promise<{ message: string }> {
    const { data } = await apiClient.post<{ message: string }>(
      `/bloggers/${id}/bind-face/${inspirationId}`,
    )
    return data
  },
}

/** 博主注销人脸 / 解绑素材的结果计数 */
export interface BloggerUnbindResult {
  blogger_id: number
  /** 注销人脸时为 false（解绑素材不返回此字段） */
  registered?: boolean
  /** 删除的人脸特征记录数（仅注销人脸） */
  embedding_deleted?: number
  /** 解除的素材归属关联数 */
  inspirations_unlinked: number
  /** 回退为未匹配的人脸检测记录数 */
  face_detections_cleared: number
}

/** 博主人脸注册状态 */
export interface BloggerFaceStatus {
  registered: boolean
  blogger_id: number
  updated_at?: string | null
}

/** 人物组信息（方案 B：同一现实人物跨平台账号） */
export interface PersonGroupInfo {
  group_id: number
  group_name: string
  primary_blogger_id: number
  member_ids?: number[]
  members?: Array<{
    id: number
    name: string
    platform: string
    platform_user_id?: string | null
    xhs_id?: string | null
    profile_url?: string | null
    avatar_path?: string | null
  }>
}

/** 单张图片的注册结果明细（部分跳过时前端逐张提示原因） */
export interface FacePhotoResult {
  index: number
  /** 来源：上传照片 / 已关联素材 */
  source?: 'upload' | 'inspiration'
  status: 'used' | 'skipped'
  reason?: 'no_face' | 'low_confidence' | 'small_face' | null
  message?: string | null
  det_score?: number | null
  face_ratio?: number | null
}

/** 博主人脸注册结果 */
export interface BloggerFaceRegisterResult extends BloggerFaceStatus {
  blogger_name?: string
  photos_used?: number
  photos_total?: number
  /** 素材来源跳过警告（文件缺失/不属于该博主等） */
  warnings?: string[]
  photo_results?: FacePhotoResult[]
}

/** 职业模特 API（/api/models）：不含人脸能力，仅通用人物能力 */

export * from './personModels'
