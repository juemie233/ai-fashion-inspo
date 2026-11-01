/** 最近搜索记录（P2-5）：搜索页初始态展示「最近搜索」，不再是空白页。
 *
 * 存在 AsyncStorage（与后端地址同一个存储），上限 10 条、去重、最新在前。
 * 不做「搜索建议」的排序学习——本地小数据量没意义，后来者也别加。
 */

import AsyncStorage from '@react-native-async-storage/async-storage'

/** 存储键（加前缀避免与后端地址、其它设置冲突） */
const STORAGE_KEY = 'search:history'
/** 最多保留多少条 */
export const MAX_HISTORY = 10

/** 读取最近搜索（失败返回空数组，不影响搜索页渲染） */
export async function loadSearchHistory(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is string => typeof item === 'string' && item.trim() !== '')
  } catch {
    return []
  }
}

/** 记录一条搜索（去重 + 置顶 + 截断），返回更新后的完整列表 */
export async function pushSearchHistory(term: string): Promise<string[]> {
  const value = term.trim()
  if (!value) return loadSearchHistory()
  const existing = await loadSearchHistory()
  const next = [value, ...existing.filter((item) => item !== value)].slice(0, MAX_HISTORY)
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 写失败不影响本次搜索
  }
  return next
}

/** 清空最近搜索 */
export async function clearSearchHistory(): Promise<void> {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY)
  } catch {
    // 忽略
  }
}
