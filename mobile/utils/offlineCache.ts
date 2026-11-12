/** 离线缓存（P1 弱网/离线体验）。
 *
 * 目标：**断网时不至于看空白** —— 打开 App 至少能看到上次看过的素材库首页，
 * 搜索页的「常用标签」也还在，并明确告诉用户「这是缓存，点重试」。
 *
 * 三条边界：
 * 1. **只缓存默认视图**（无筛选、按最新排序）的首页 30 条：缓存别的筛选组合既占空间，
 *    又容易在数据变化后给用户看到过期列表；非默认视图失败只提示错误；
 * 2. **有保质期**（默认 7 天）：太旧的缓存容易误导（素材可能已被删除/改动）；
 * 3. **只做读缓存，不做离线写**：上传/收藏等写操作断网就是失败，不排队重放
 *    （规划 §6 明确「完整离线写同步」不做）。
 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import type { Inspiration, ListFilter, SortKey } from '../services/api'

/** 缓存键 */
const GALLERY_KEY = 'offline:gallery'
const POPULAR_TAGS_KEY = 'offline:popular-tags'

/** 默认保质期：7 天 */
export const DEFAULT_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000

/** 缓存条数上限（与首页分页一致，避免把 AsyncStorage 撑爆） */
const MAX_CACHED_ITEMS = 30

/** 素材库首页缓存 */
export interface GalleryCacheEntry {
  /** 写入时间（毫秒） */
  savedAt: number
  items: Inspiration[]
  total: number
}

/** 搜索页常用标签缓存 */
export interface PopularTagsCacheEntry {
  savedAt: number
  names: string[]
}

/** 缓存是否仍然可用（未过期 + 结构合法） */
export function isCacheFresh(
  savedAt: number | null | undefined,
  now = Date.now(),
  ttlMs = DEFAULT_CACHE_TTL_MS,
): boolean {
  if (typeof savedAt !== 'number' || !Number.isFinite(savedAt)) return false
  const age = now - savedAt
  return age >= 0 && age <= ttlMs
}

/** 只有「无筛选 + 最新排序」的首页才写缓存（其它视图不缓存） */
export function shouldCache(filter: ListFilter, sort: SortKey): boolean {
  return Object.keys(filter).length === 0 && sort === 'newest'
}

/** 写入素材库首页缓存（裁剪到上限、剔除脏数据；失败静默） */
export async function saveGalleryCache(items: Inspiration[], total: number): Promise<void> {
  try {
    const clean = (Array.isArray(items) ? items : [])
      .filter((item) => item && typeof item.id === 'string')
      .slice(0, MAX_CACHED_ITEMS)
    if (clean.length === 0) return
    const entry: GalleryCacheEntry = { savedAt: Date.now(), items: clean, total }
    await AsyncStorage.setItem(GALLERY_KEY, JSON.stringify(entry))
  } catch {
    // 缓存失败不影响正常浏览
  }
}

/** 读取素材库缓存（过期/损坏返回 null） */
export async function loadGalleryCache(
  now = Date.now(),
): Promise<GalleryCacheEntry | null> {
  try {
    const raw = await AsyncStorage.getItem(GALLERY_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<GalleryCacheEntry>
    if (!Array.isArray(parsed.items) || !isCacheFresh(parsed.savedAt, now)) return null
    return {
      savedAt: parsed.savedAt as number,
      items: parsed.items as Inspiration[],
      total: typeof parsed.total === 'number' ? parsed.total : parsed.items.length,
    }
  } catch {
    return null
  }
}

/** 写入常用标签缓存（只存名字，搜索页初始态用） */
export async function savePopularTags(names: string[]): Promise<void> {
  try {
    const clean = (Array.isArray(names) ? names : []).filter((n) => typeof n === 'string' && n)
    if (clean.length === 0) return
    const entry: PopularTagsCacheEntry = { savedAt: Date.now(), names: clean.slice(0, 12) }
    await AsyncStorage.setItem(POPULAR_TAGS_KEY, JSON.stringify(entry))
  } catch {
    // 忽略
  }
}

/** 读取常用标签缓存（过期/损坏返回空数组） */
export async function loadPopularTags(now = Date.now()): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(POPULAR_TAGS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Partial<PopularTagsCacheEntry>
    if (!Array.isArray(parsed.names) || !isCacheFresh(parsed.savedAt, now)) return []
    return parsed.names.filter((n): n is string => typeof n === 'string')
  } catch {
    return []
  }
}

/** 清空离线缓存（设置页「清除缓存」调用） */
export async function clearOfflineCache(): Promise<void> {
  try {
    await AsyncStorage.multiRemove([GALLERY_KEY, POPULAR_TAGS_KEY])
  } catch {
    // 忽略
  }
}

/** 缓存时间的中文展示（例如「11-12 14:02」） */
export function formatCacheTime(savedAt: number): string {
  if (!Number.isFinite(savedAt)) return ''
  const date = new Date(savedAt)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
