/** 「抖音收藏」收藏夹选择的**本地记忆**（只保留最近一次，覆盖写）。

为什么需要：每次扫描后都要重新勾掉不想要的夹（实测 31 个夹里混着「股票 / 哲学 /
历史」这类永远不想要的），这个选择该被记住。但**只记最近一次**——收藏夹会增删，
历史选择没有追溯价值，多存几份只会让「这次到底按哪次下的」变模糊。

存储：`localStorage['f2-collect-folders']`，值为 `{ ids, savedAt }`。解析失败或形状
不对一律当作「没有记录」——脏数据不该卡住扫描流程。

写入时机：**确认下载时**。只扫描不下载不动它，避免「点开看一眼」就把上次的选择冲掉。
*/

/** localStorage 键名（与仓库其它偏好一致：kebab-case）。 */
const STORAGE_KEY = 'f2-collect-folders'

export interface F2FolderSelection {
  /** 上次确认下载时勾选的收藏夹 ID */
  ids: string[]
  /** 记录时间（ISO 字符串；只用于界面提示） */
  savedAt: string
}

export interface RestoredSelection {
  /** 这次弹窗里应该勾选的收藏夹 ID */
  ids: string[]
  /** 是否来自上次记录（false = 回退成默认全选） */
  fromSaved: boolean
  /** 上次记录里有、但这次扫描已经找不到的夹数（夹被删/改名） */
  staleCount: number
}

/**
 * 读取上次的收藏夹选择。
 *
 * @returns 有可用记录时返回它；没有/解析失败/形状不对时返回 null（＝默认全选）。
 */
export function loadFolderSelection(): F2FolderSelection | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { ids?: unknown; savedAt?: unknown }
    const ids = Array.isArray(parsed?.ids)
      ? parsed.ids.filter((id): id is string => typeof id === 'string' && Boolean(id))
      : []
    if (!ids.length) return null
    return { ids, savedAt: typeof parsed?.savedAt === 'string' ? parsed.savedAt : '' }
  } catch {
    // JSON 脏数据，或隐私模式下 localStorage 不可用：都当作「没有记录」
    return null
  }
}

/**
 * 覆盖保存本次选择（**只保留一次**，不累积历史）。
 *
 * 写失败（隐私模式 / 配额满）静默忽略：记忆只是便利，绝不能影响下载。
 *
 * @param ids 本次勾选的收藏夹 ID（自动去重、丢弃空值）。
 */
export function saveFolderSelection(ids: string[], now: Date = new Date()): void {
  try {
    const cleaned = [...new Set(ids.filter(Boolean))]
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ids: cleaned, savedAt: now.toISOString() }))
  } catch {
    /* 存不下就算了 */
  }
}

/**
 * 把「上次的选择」映射到「这次扫描到的夹」上。
 *
 * 只在仍然存在的夹里恢复（夹被删/改名后按 ID 找不到，不能凭空勾一个不存在的夹）；
 * 一个都不剩或压根没有记录时回退成**默认全选**。
 *
 * @param saved 上次记录（可为 null）。
 * @param availableIds 本次扫描到的收藏夹 ID。
 */
export function restoreFolderSelection(
  saved: F2FolderSelection | null,
  availableIds: string[],
): RestoredSelection {
  const all = [...availableIds]
  if (!saved || !saved.ids.length) return { ids: all, fromSaved: false, staleCount: 0 }

  const available = new Set(all)
  const kept = saved.ids.filter((id) => available.has(id))
  const staleCount = saved.ids.length - kept.length
  if (!kept.length) return { ids: all, fromSaved: false, staleCount }
  return { ids: kept, fromSaved: true, staleCount }
}
