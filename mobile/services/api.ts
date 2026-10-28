/** API 客户端：封装 Axios，管理后端地址（支持设置页自定义并持久化）。 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import axios from 'axios'
import { Platform } from 'react-native'
import type { PaginatedResponse } from '@shared/types/api'
import type { AnalysisStatus, MediaType, SourceType } from '@shared/types/inspiration'
import type { Tag, TagCategoryGroup } from '@shared/types/tag'

// Android 模拟器使用 10.0.2.2，iOS 模拟器用 localhost，真机用局域网 IP（设置页可改）
const DEFAULT_IP = Platform.select({
  android: '10.0.2.2',
  ios: 'localhost',
  default: 'localhost',
})

const DEFAULT_PORT = '18888'

/** 默认后端地址 */
const DEFAULT_BASE_URL = `http://${DEFAULT_IP}:${DEFAULT_PORT}`

/** 持久化存储键：自定义后端地址 */
const API_URL_STORAGE_KEY = 'fashion-inspo:api-base-url'

/** 当前 API 基础地址（模块级缓存；请求拦截器读取，设置页保存后更新） */
let currentApiBaseUrl = DEFAULT_BASE_URL

/** 去掉首尾空白与末尾斜杠，返回规范化地址；空串回退默认值。
 *
 * 没写协议时自动补 `http://`：真机上用户通常直接输入「192.168.31.142:18888」，
 * axios 会把它当相对路径直接失败（2026-10-23 真机排查时踩到）。
 */
function normalizeBaseUrl(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, '')
  if (!trimmed) return DEFAULT_BASE_URL
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
}

/** 初始化：从持久化存储恢复自定义后端地址（App 启动时调用一次） */
export async function loadApiBaseUrl(): Promise<string> {
  try {
    const saved = await AsyncStorage.getItem(API_URL_STORAGE_KEY)
    if (saved && saved.trim()) {
      currentApiBaseUrl = normalizeBaseUrl(saved)
    }
  } catch {
    // 读取失败（存储异常）静默使用默认地址
  }
  return currentApiBaseUrl
}

/** 保存自定义后端地址（设置页调用）；保存失败不影响本次会话（模块缓存已更新） */
export async function saveApiBaseUrl(url: string): Promise<void> {
  currentApiBaseUrl = normalizeBaseUrl(url)
  try {
    await AsyncStorage.setItem(API_URL_STORAGE_KEY, currentApiBaseUrl)
  } catch {
    // 持久化失败静默：本次运行仍生效，重启后回退
  }
}

/** 当前 API 基础地址（同步读取，供文件 URL 拼接与设置页展示） */
export function getApiBaseUrl(): string {
  return currentApiBaseUrl
}

/** 测试后端连通性：GET /api/health，返回是否可连（设置页「测试连接」用） */
export async function testApiConnection(
  baseUrl: string,
): Promise<{ ok: boolean; message: string }> {
  try {
    const normalized = normalizeBaseUrl(baseUrl)
    const resp = await axios.get(`${normalized}/api/health`, { timeout: 8000 })
    if (resp.status === 200) {
      return { ok: true, message: '连接成功' }
    }
    return { ok: false, message: `后端返回 HTTP ${resp.status}` }
  } catch (e) {
    return { ok: false, message: '无法连接，请检查地址与网络' }
  }
}

/** Axios 实例：baseURL 由请求拦截器按当前地址动态注入（支持运行中切换后端） */
export const apiClient = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  config.baseURL = `${currentApiBaseUrl}/api`
  return config
})

/** 拼接素材文件访问地址：按路径段 URL 编码，防止含空格/中文的路径 404。
 *
 * 入参做类型兜底：后端字段脏掉（数组/对象/数字）时直接 `.split` 会抛
 * "relativePath.split is not a function"，整页崩溃；这里退化成空路径（图裂但页面不崩）。
 */
export function getFileUrl(base: string, relativePath: unknown): string {
  const path = typeof relativePath === 'string' ? relativePath : ''
  const encoded = path.split('/').map(encodeURIComponent).join('/')
  return `${base}/api/files/${encoded}`
}

// 类型定义
/** 单条素材（后端 `/api/inspirations` 返回）。
 *
 * 枚举与标签结构来自 `shared/types`（Web 与 Mobile 共用一份）；实体本身仍留在这里：
 * web 的 `InspirationOut` 字段更多（标签分组、人物关联、相似度等），两端实体尚未一致，
 * 强行合并会把 mobile 没用的字段也拉进来。
 */
export interface Inspiration {
  id: string
  source_type: SourceType
  source_url?: string | null
  source_author?: string | null
  source_platform_id?: string | null
  file_path: string
  thumbnail_path?: string | null
  media_type: MediaType
  dominant_colors?: string | null
  is_favorite: boolean
  created_at: string
  updated_at?: string | null
  tags: InspirationTag[]
  analysis_status?: AnalysisStatus | null
}

/** 素材上带置信度的标签关联（后端独有结构，shared 未定义） */
export interface InspirationTag {
  tag: Tag
  /** AI 判定置信度 0~1 */
  confidence: number
}

/** 分页响应（与后端分页约定一致） */
export type InspirationListResponse = PaginatedResponse<Inspiration>

// ---------------------------------------------------------------------------
// M2 能力：标签编辑 / 移入垃圾桶 / 相似推荐 / 多图上传 / 任务中心
// ---------------------------------------------------------------------------

/** 扩展名 → MIME（HEIC 也按 jpeg 报，避免后端拒绝） */
const MIME_BY_EXT: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  gif: 'image/gif',
  heic: 'image/jpeg',
}

/** 上传单张图片（multipart）；onProgress 为 0~1 的上传进度回调 */
export async function uploadInspirationImage(
  uri: string,
  onProgress?: (ratio: number) => void,
): Promise<Inspiration> {
  const formData = new FormData()
  const filename = uri.split('/').pop() || 'photo.jpg'
  const ext = filename.split('.').pop()?.toLowerCase() || 'jpg'
  const mime = MIME_BY_EXT[ext] || 'image/jpeg'

  formData.append('file', { uri, name: filename, type: mime } as unknown as Blob)
  formData.append('source_type', 'manual_upload')

  const { data } = await apiClient.post<Inspiration>('/inspirations', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // axios 的进度回调在 RN 下由 XMLHttpRequest 提供；部分环境可能不触发，回调可选
    onUploadProgress: (e) => {
      if (!onProgress || !e.total) return
      onProgress(Math.min(1, e.loaded / e.total))
    },
  })
  return data
}

/** 标签关联结果（与后端 ``add_inspiration_tags`` 的返回一致） */
export interface AddTagsResult {
  /** 实际新增的关联（**是对象数组**，不是标签名数组） */
  added?: Array<{ id: number; name: string; category: string }>
  /** 新增条数 */
  count?: number
  /** 同名标签类别与请求类别不符（仍会关联，仅提示） */
  category_mismatch?: Array<{
    id: number
    name: string
    category: string
    requested: string
    already_linked: boolean
  }>
}

/** 给单条素材关联标签（按名称查找或创建，已关联的自动跳过） */
export async function addTagsToInspiration(
  id: string,
  names: string[],
  category = 'free',
): Promise<AddTagsResult> {
  const { data } = await apiClient.post<AddTagsResult>(`/inspirations/${id}/tags`, {
    names,
    category,
    source: 'manual',
  })
  return data ?? {}
}

/** 解除素材与某个标签的关联（tagId 为数字主键） */
export async function removeTagFromInspiration(id: string, tagId: number): Promise<void> {
  await apiClient.delete(`/inspirations/${id}/tags/${tagId}`)
}

/** 垃圾桶原因枚举（与后端一致） */
export type TrashReason = '质量差' | '重复' | '不喜欢' | '隐私' | '其他'

/** 把素材移入垃圾桶（软删除，可恢复） */
export async function trashInspiration(id: string, reason?: TrashReason): Promise<Inspiration> {
  const { data } = await apiClient.post<Inspiration>(`/inspirations/${id}/trash`, {
    ...(reason ? { reason } : {}),
    source: 'manual',
  })
  return data
}

/** 从垃圾桶恢复 */
export async function restoreInspiration(id: string): Promise<Inspiration> {
  const { data } = await apiClient.post<Inspiration>(`/inspirations/${id}/restore`)
  return data
}

/** 相似素材推荐（向量检索结果） */
export async function fetchSimilarInspirations(id: string): Promise<Inspiration[]> {
  const { data } = await apiClient.get<{ items?: Inspiration[]; results?: Inspiration[] }>(
    `/search/similar/${id}`,
  )
  // 后端可能返回 {items} 或 {results}，两种都兼容
  return data?.items ?? data?.results ?? []
}

/**
 * 标签库缓存：`GET /api/tags` 有 **2 万+ 条、约 4.3MB、8 秒**（实测），
 * 三个页面（标签管理 / 标签筛选 / 标签编辑器）都要用，必须缓存，否则每次进页面都重拉。
 */
let tagGroupsCache: TagCategoryGroup[] | null = null

/** 标签全量（按类别分组）；默认走缓存，force=true 强制刷新 */
export async function fetchTagGroups(force = false): Promise<TagCategoryGroup[]> {
  if (!force && tagGroupsCache) return tagGroupsCache
  const { data } = await apiClient.get<TagCategoryGroup[]>('/tags')
  tagGroupsCache = data ?? []
  return tagGroupsCache
}

/** 清空标签缓存（改名/合并后可调用，让下次进页面拿到最新数据） */
export function invalidateTagCache(): void {
  tagGroupsCache = null
}

/** 新建标签 */
export async function createTag(name: string, category = 'free'): Promise<Tag> {
  const { data } = await apiClient.post<Tag>('/tags', { name, category })
  return data
}

/** 后台任务（AI 分析、采集、出图等共用一套任务队列） */
export interface TaskItem {
  id: number
  type: string
  status: 'pending' | 'running' | 'paused' | 'success' | 'failed' | 'cancelled' | string
  progress: number
  total: number
  done: number
  error?: string | null
  created_at: string
  updated_at: string
}

/** 任务分页列表 */
export async function fetchTasks(page = 1, size = 20): Promise<PaginatedResponse<TaskItem>> {
  const { data } = await apiClient.get<PaginatedResponse<TaskItem>>('/tasks', {
    params: { page, size },
  })
  return data
}

/** 取消任务（仅 pending/running 有效） */
export async function cancelTask(id: number): Promise<void> {
  await apiClient.post(`/tasks/${id}/cancel`)
}

export type { Tag, TagCategoryGroup }

// ---------------------------------------------------------------------------
// M3：列表筛选/排序、批量操作、以图搜图、合集
// ---------------------------------------------------------------------------

/** 素材列表筛选条件（与后端 `GET /inspirations` 查询参数对齐） */
export interface ListFilter {
  /** 只看收藏 */
  is_favorite?: boolean
  /** tagged=已标注 / untagged=未标注 */
  tag_status?: 'tagged' | 'untagged'
  /** 来源类型（source_type） */
  source_type?: string
}

/** 排序键（后端 sort 支持的子集，其余留给 Web） */
export type SortKey = 'newest' | 'oldest' | 'rating' | 'random'

/** 拉取素材分页（带筛选与排序） */
export async function fetchInspirationPage(
  page: number,
  size: number,
  filter: ListFilter = {},
  sort: SortKey = 'newest',
): Promise<InspirationListResponse> {
  const { data } = await apiClient.get<InspirationListResponse>('/inspirations', {
    params: { page, size, sort, ...filter },
  })
  return data
}

/** 批量收藏 / 取消收藏 */
export async function batchFavorite(
  ids: string[],
  isFavorite: boolean,
): Promise<{ updated?: number }> {
  const { data } = await apiClient.post<{ updated?: number }>('/inspirations/batch-favorite', {
    ids,
    is_favorite: isFavorite,
  })
  return data ?? {}
}

/** 批量移入垃圾桶（软删除，可恢复） */
export async function batchTrash(
  ids: string[],
  reason?: TrashReason,
): Promise<{ trashed?: number; skipped?: number }> {
  const { data } = await apiClient.post<{ trashed?: number; skipped?: number }>(
    '/inspirations/batch-trash',
    reason ? { ids, reason, source: 'manual' } : { ids },
  )
  return data ?? {}
}

/** 向量检索单项：素材 + 相似度分数 */
export interface VectorHit {
  inspiration: Inspiration
  score: number
}

/** 语义检索（一句话 → 文本向量；需要 Ollama + 嵌入模型就绪） */
export async function vectorSearchByText(text: string, topK = 20): Promise<VectorHit[]> {
  const form = new FormData()
  form.append('text', text)
  form.append('top_k', String(topK))
  const { data } = await apiClient.post<{ items?: VectorHit[] }>('/search/vector', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // 文本嵌入比图像快，但仍要给模型留时间
    timeout: 45000,
  })
  return data?.items ?? []
}

/** 以图搜图（相册/相机图片 → CLIP 图像向量） */
export async function vectorSearchByImage(uri: string, topK = 20): Promise<VectorHit[]> {
  const form = new FormData()
  const filename = uri.split('/').pop() || 'query.jpg'
  const ext = filename.split('.').pop()?.toLowerCase() || 'jpg'
  form.append('file', {
    uri,
    name: filename,
    type: MIME_BY_EXT[ext] || 'image/jpeg',
  } as unknown as Blob)
  form.append('top_k', String(topK))
  const { data } = await apiClient.post<{ items?: VectorHit[] }>('/search/vector', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000, // 以图搜图要跑一次图像向量推理，给足时间
  })
  return data?.items ?? []
}

/** 合集（收藏夹）：一级/二级，手动/智能 */
export interface CollectionItem {
  id: number
  name: string
  description?: string | null
  kind: string
  parent_id?: number | null
  item_count?: number | null
  cover_thumbnail_path?: string | null
  auto_source?: string | null
  child_count?: number
  created_at?: string
}

/** 合集列表 */
export async function fetchCollections(): Promise<CollectionItem[]> {
  const { data } = await apiClient.get<CollectionItem[]>('/collections')
  return data ?? []
}

/** 新建合集 */
export async function createCollection(name: string): Promise<CollectionItem> {
  const { data } = await apiClient.post<CollectionItem>('/collections', { name })
  return data
}

/** 合集内素材（分页） */
export async function fetchCollectionInspirations(
  id: number,
  page = 1,
  size = 30,
): Promise<InspirationListResponse> {
  const { data } = await apiClient.get<{ items: Inspiration[]; total: number }>(
    `/collections/${id}/inspirations`,
    { params: { page, page_size: size } },
  )
  return { items: data?.items ?? [], total: data?.total ?? 0, page, size }
}

/** 批量加入合集 */
export async function addToCollection(id: number, ids: string[]): Promise<void> {
  await apiClient.post(`/collections/${id}/inspirations`, { inspiration_ids: ids })
}

/** 批量移出合集 */
export async function removeFromCollection(id: number, ids: string[]): Promise<void> {
  await apiClient.delete(`/collections/${id}/inspirations`, { data: { inspiration_ids: ids } })
}

// ---------------------------------------------------------------------------
// M4：人物浏览（穿搭博主 / 模特）+ 标签轻量管理
// ---------------------------------------------------------------------------

/** 人物类型：后端拆成 bloggers / models 两张表 */
export type PersonKind = 'blogger' | 'model'

/** 人物统一视图（两种表字段取并集，缺的置空） */
export interface Person {
  id: number
  kind: PersonKind
  name: string
  platform: string
  avatar_path?: string | null
  bio?: string | null
  ip_location?: string | null
  profile_url?: string | null
  /** 素材数 */
  inspiration_count: number
  /** 最近一次入库时间 */
  last_inspiration_at?: string | null
  /** 是否注册了人脸（博主才有意义） */
  face_registered?: boolean
  /** 粉丝数快照（仅博主，来自平台统计同步） */
  follower_count?: number | null
}

/** 后端人物原始结构（两份表字段并集） */
interface RawPerson {
  id: number
  name?: string
  platform?: string
  avatar_path?: string | null
  bio?: string | null
  ip_location?: string | null
  profile_url?: string | null
  inspiration_count?: number
  last_inspiration_at?: string | null
  face_registered?: boolean
  follower_count?: number | null
}

/** 把后端两种人物结构归一，页面只认 `Person` */
function toPerson(raw: RawPerson, kind: PersonKind): Person {
  return {
    id: raw.id,
    kind,
    name: String(raw.name ?? '未命名'),
    platform: String(raw.platform ?? 'other'),
    avatar_path: raw.avatar_path ?? null,
    bio: raw.bio ?? null,
    ip_location: raw.ip_location ?? null,
    profile_url: raw.profile_url ?? null,
    inspiration_count: Number(raw.inspiration_count ?? 0),
    last_inspiration_at: raw.last_inspiration_at ?? null,
    face_registered: Boolean(raw.face_registered),
    follower_count: raw.follower_count ?? null,
  }
}

/** 人物分页（type 决定查 bloggers 还是 models） */
export async function fetchPersons(
  kind: PersonKind,
  page = 1,
  size = 20,
  search?: string,
): Promise<PaginatedResponse<Person>> {
  const path = kind === 'blogger' ? '/bloggers' : '/models'
  const params: Record<string, string | number> = { page, size, sort: 'count' }
  if (search && search.trim()) params.search = search.trim()

  const { data } = await apiClient.get<{ items?: RawPerson[]; total?: number }>(path, { params })
  return {
    items: (data?.items ?? []).map((it) => toPerson(it, kind)),
    total: Number(data?.total ?? 0),
    page,
    size,
  }
}

/** 单个人物详情（`GET /bloggers|models/{id}`）
 *
 * 详情页头需要头像/简介/素材数：按 id 直接取，避免「拉列表前 200 条再找」——
 * 素材数排名靠后的人物会找不到，白白多传 200 条数据。
 */
export async function fetchPerson(kind: PersonKind, id: number): Promise<Person | null> {
  const path = kind === 'blogger' ? `/bloggers/${id}` : `/models/${id}`
  const { data } = await apiClient.get<RawPerson>(path)
  return data ? toPerson(data, kind) : null
}

/** 人物作品墙（分页） */
export async function fetchPersonInspirations(
  kind: PersonKind,
  id: number,
  page = 1,
  size = 30,
): Promise<InspirationListResponse> {
  const path = kind === 'blogger' ? `/bloggers/${id}/inspirations` : `/models/${id}/inspirations`
  const { data } = await apiClient.get<{ items?: Inspiration[]; total?: number }>(path, {
    params: { page, size },
  })
  return { items: data?.items ?? [], total: Number(data?.total ?? 0), page, size }
}

/** 标签改名（轻量管理只开放改名，合并/删除留在 Web） */
export async function renameTag(tagId: number, name: string): Promise<Tag> {
  const { data } = await apiClient.patch<Tag>(`/tags/${tagId}`, { name })
  return data
}

// ---------------------------------------------------------------------------
// M5：本地出图记录（只读）+ 分享入库
// ---------------------------------------------------------------------------

/** 出图批次（后端 `GET /generate/runs`） */
export interface GenerateRun {
  id: number
  kind: string
  model_key?: string | null
  status: string
  prompt: string
  negative?: string | null
  total: number
  done: number
  failed: number
  imported_count: number
  files_missing?: boolean
  created_at: string
}

/** 候选图（`GET /generate/runs/{id}` 的 items 项） */
export interface GenerateCandidate {
  filename: string
  index: number
  seed?: number | null
  size_bytes?: number | null
  imported: boolean
  /** 后端返回的相对路径，如 /api/generate/runs/111/candidates/01.png */
  url: string
}

/** 批次详情 */
export interface GenerateRunDetail {
  run: GenerateRun
  candidates: GenerateCandidate[]
}

/** 档位展示名缓存：`GET /generate/models` 是 MODEL_SPECS 的单一来源 */
let generateModelLabels: Record<string, string> | null = null

/** 拉出图档位展示名（会话级缓存；失败时由调用方回退内置兜底表） */
export async function fetchGenerateModelLabels(): Promise<Record<string, string>> {
  if (generateModelLabels) return generateModelLabels
  const { data } = await apiClient.get<{
    items?: Array<{ key: string; label: string }>
  }>('/generate/models')
  const labels: Record<string, string> = {}
  for (const item of data?.items ?? []) {
    // 与 Web 的 shortModelLabel 同口径：去掉「（快，1024² 约 6 秒）」这类补充
    labels[item.key] = item.label.split('（')[0] ?? item.label
  }
  generateModelLabels = labels
  return labels
}

/** 出图批次分页 */
export async function fetchGenerateRuns(
  page = 1,
  size = 20,
): Promise<PaginatedResponse<GenerateRun>> {
  const { data } = await apiClient.get<{ items?: GenerateRun[]; total?: number }>('/generate/runs', {
    params: { page, size },
  })
  return { items: data?.items ?? [], total: Number(data?.total ?? 0), page, size }
}

/** 批次详情 + 候选图（后端字段是 `{items, run, task}`，这里归一成 {run, candidates}） */
export async function fetchGenerateRun(id: number): Promise<GenerateRunDetail> {
  const { data } = await apiClient.get<{
    items?: GenerateCandidate[]
    run?: GenerateRun
  }>(`/generate/runs/${id}`)
  return {
    run: data?.run ?? ({} as GenerateRun),
    candidates: data?.items ?? [],
  }
}

/** 候选图完整 URL（后端给的是 /api/... 相对路径，w>0 时请求缩略图） */
export function candidateUrl(base: string, url: string, width = 0): string {
  const path = typeof url === 'string' && url ? url : ''
  if (!path) return ''
  const withHost = /^https?:\/\//i.test(path) ? path : `${base}${path}`
  return width > 0 ? `${withHost}?w=${width}` : withHost
}

/** 从 URL 下载并入库（分享文本链接时用） */
export async function importInspirationFromUrl(url: string): Promise<Inspiration> {
  const { data } = await apiClient.post<Inspiration>('/inspirations/from-url', { url })
  return data
}
