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

/** 收藏动态条目：一条收藏/取消收藏流水 */
export interface FavoriteHistoryItem {
  id: number
  inspiration_id: string
  /** 动作：favorite / unfavorite（兼容 favorited/unfavorited 等历史取值） */
  action: string
  /** 操作来源（后端记录的操作入口，未知值回退原样展示） */
  source?: string | null
  /** 动作发生时间 */
  created_at: string
  /** 关联素材快照（素材被彻底删除时可能为 null） */
  item: (InspirationOut & { favorited_at?: string | null }) | null
}

interface FavoriteHistoryOut {
  items: FavoriteHistoryItem[]
}

/** 收藏动作中文名（未知值原样展示，避免吞掉后端新增的动作） */
const FAVORITE_ACTION_LABELS: Record<string, string> = {
  favorite: '收藏',
  favorited: '收藏',
  add: '收藏',
  unfavorite: '取消收藏',
  unfavorited: '取消收藏',
  remove: '取消收藏',
}

/** 收藏来源中文名（未知值原样展示） */
const FAVORITE_SOURCE_LABELS: Record<string, string> = {
  detail: '素材详情',
  card: '素材卡片',
  batch: '批量操作',
  collection: '合集操作',
  upload: '上传',
  api: '接口',
  manual: '手动',
  auto: '自动',
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
