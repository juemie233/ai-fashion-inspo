/** 收藏中心 API：收藏概览（统计 + 最近收藏）与收藏动态（收藏/取消收藏的流水）。
 *
 * 与「收藏合集」不同：这里不关心素材归在哪个合集，而是看**收藏行为本身**——
 * 总收藏、本周新增、未评分收藏、未整理收藏，以及最近的收藏/取消动态。
 */

import apiClient from './client'
import type { InspirationOut } from './inspirations'

/** 收藏概览响应 */
export interface FavoritesOverviewOut {
  /** 当前收藏素材总数 */
  total_favorites: number
  /** 最近 7 天新增收藏数 */
  favorites_this_week: number
  /** 收藏但还没有评分（rating 为空/0）的素材数 */
  unrated_favorites: number
  /** 收藏但未归入任何手动合集的素材数 */
  unfiled_favorites: number
  /** 最近收藏的素材（按收藏时间倒序，数量由 recent_limit 决定） */
  recent: InspirationOut[]
}

/** 收藏动态里的素材快照：后端只返回这几个字段，**不是**完整 InspirationOut */
export interface FavoriteHistoryReferencedItem {
  id: string
  file_path: string
  thumbnail_path: string | null
  media_type: string
  source_author: string | null
  is_favorite: boolean
  /** 收藏时间（历史回填来源可能是近似值） */
  favorited_at: string | null
}

/** 收藏动态条目：一条收藏/取消收藏流水 */
export interface FavoriteHistoryItem {
  id: number
  inspiration_id: string
  /** 动作：favorite / unfavorite */
  action: string
  /** 操作来源（web / batch / api / legacy；未知值原样展示） */
  source?: string | null
  /** 动作发生时间 */
  created_at: string
  /** 关联素材快照（事件级联删除后不会出现，保留 null 兜底） */
  item: FavoriteHistoryReferencedItem | null
}

interface FavoriteHistoryOut {
  items: FavoriteHistoryItem[]
}

/** 收藏动作中文名（后端只写 favorite / unfavorite；未知值原样展示） */
const FAVORITE_ACTION_LABELS: Record<string, string> = {
  favorite: '收藏',
  unfavorite: '取消收藏',
}

/** 收藏来源中文名：与后端 FavoriteEvent.source 的取值一一对应（未知值原样展示） */
const FAVORITE_SOURCE_LABELS: Record<string, string> = {
  web: '网页',
  batch: '批量操作',
  api: '接口',
  legacy: '历史收藏',
}

export function favoriteActionLabel(action: string): string {
  return FAVORITE_ACTION_LABELS[action] ?? action
}

export function favoriteSourceLabel(source?: string | null): string {
  if (!source) return '未知来源'
  return FAVORITE_SOURCE_LABELS[source] ?? source
}

/** 是否「取消收藏」动作（历史列表据此决定标签颜色/是否可跳详情） */
export function isUnfavoriteAction(action: string): boolean {
  return favoriteActionLabel(action) === '取消收藏'
}

/** 收藏概览：统计卡 + 最近收藏（默认 12 条） */
export async function fetchFavoritesOverview(recentLimit = 12): Promise<FavoritesOverviewOut> {
  const { data } = await apiClient.get<FavoritesOverviewOut>('/favorites/overview', {
    params: { recent_limit: recentLimit },
  })
  return data
}

/** 收藏动态：最近 limit 条收藏/取消流水（默认 30 条） */
export async function fetchFavoritesHistory(limit = 30): Promise<FavoriteHistoryOut> {
  const { data } = await apiClient.get<FavoriteHistoryOut>('/favorites/history', {
    params: { limit },
  })
  return data
}

// ── 待整理工作台：智能视图 + cleanup 摘要 ──

/** 智能视图 key：未评分 / 未整理 / 高分 / 收藏超 90 天 */
type FavoriteViewKey = 'unrated' | 'unfiled' | 'high_rating' | 'stale'

/** 智能视图卡片（GET /favorites/views） */
export interface FavoriteViewOut {
  key: FavoriteViewKey | string
  label: string
  description: string
  count: number
}

interface FavoriteViewsOut {
  views: FavoriteViewOut[]
}

/** 智能视图素材分页（GET /favorites/views/{key}?page&size，与全局分页约定一致） */
interface FavoriteViewItemsOut {
  key: string
  items: InspirationOut[]
  total: number
  page: number
  size: number
}

/** cleanup 统计 */
interface FavoriteCleanupCounts {
  unrated: number
  unfiled: number
  missing_files: number
  duplicate_groups: number
  duplicate_items: number
}

/** 重复组：同一内容 hash 的多份收藏 */
export interface FavoriteDuplicateGroup {
  content_hash: string
  items: InspirationOut[]
}

/** 待整理摘要（GET /favorites/cleanup） */
export interface FavoriteCleanupOut {
  counts: FavoriteCleanupCounts
  missing_files: InspirationOut[]
  duplicate_groups: FavoriteDuplicateGroup[]
}

export async function fetchFavoriteViews(): Promise<FavoriteViewsOut> {
  const { data } = await apiClient.get<FavoriteViewsOut>('/favorites/views')
  return data
}

export async function fetchFavoriteViewItems(
  key: string,
  page = 1,
  pageSize = 24,
): Promise<FavoriteViewItemsOut> {
  const { data } = await apiClient.get<FavoriteViewItemsOut>(`/favorites/views/${key}`, {
    params: { page, size: pageSize },
  })
  return data
}

export async function fetchFavoritesCleanup(): Promise<FavoriteCleanupOut> {
  const { data } = await apiClient.get<FavoriteCleanupOut>('/favorites/cleanup')
  return data
}
