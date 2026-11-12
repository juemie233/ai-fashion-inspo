/** 上传队列的持久化与本地文件探测（P0-1）。
 *
 * 背景：采集页的队列原来只在组件 `useState` 里，App 被杀 / 切后台过久 / 息屏
 * 之后未完成的上传直接丢失，用户以为「已经传上去了」。
 *
 * 策略（与规划文档一致）：
 * - **只持久化未完成项**（pending / error）：已完成的已经入库，留在本地没有意义，
 *   还会越积越多；
 * - 进程被杀时正在上传的那张（uploading）恢复时降级为 pending 重传——上传接口
 *   是幂等的（内容判重在后端），重传不会产生重复素材；
 * - 恢复后先探测本地文件是否还在（`expo-image` 的 `loadAsync`），不在的标记失败
 *   并给出可读原因，避免用户对着一个永远失败的任务重试。
 *
 * 不做的：真正的 Android 后台长传（需要 foreground service / WorkManager，
 * 功耗与复杂度都高）；这里只保证「回到前台能继续」。
 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import { Image } from 'expo-image'

/** 队列项状态：pending 待传 / uploading 上传中 / done 成功 / error 失败 */
export type UploadStatus = 'pending' | 'uploading' | 'done' | 'error'

/** 队列项 */
export interface UploadQueueItem {
  /** 本地临时 URI（唯一键） */
  uri: string
  status: UploadStatus
  /** 0~1 上传进度 */
  progress: number
  error?: string
  /** 入队时间（毫秒），恢复后按它保持原顺序 */
  addedAt: number
}

/** 持久化键（与后端地址、搜索历史同一套 AsyncStorage） */
const STORAGE_KEY = 'upload:queue'

/** 本地文件探测超时（毫秒）：图片解码失败/文件不在时不应该卡住恢复流程 */
const PROBE_TIMEOUT_MS = 3000

/** 本地文件丢失时写入的错误原因（页面上可读，不要写英文/异常栈） */
export const MISSING_FILE_REASON = '本地文件已不存在，请重新选择后再传'

/**
 * 把从存储里读到的任意值规整成队列：
 * - 丢弃结构不对的项（历史版本/手改存储）；
 * - `uploading` 降级为 `pending`（进程被杀时的那一张）；
 * - `done` 直接丢弃（已入库，没必要恢复）；
 * - 按 `addedAt` 升序，保持用户当初的选择顺序。
 */
export function normalizeRestoredQueue(raw: unknown): UploadQueueItem[] {
  if (!Array.isArray(raw)) return []
  const items: UploadQueueItem[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue
    const value = entry as Record<string, unknown>
    const uri = typeof value.uri === 'string' ? value.uri : ''
    if (!uri) continue
    const status = value.status
    if (status === 'done') continue
    const nextStatus: UploadStatus = status === 'error' ? 'error' : 'pending'
    items.push({
      uri,
      status: nextStatus,
      progress: 0,
      error: typeof value.error === 'string' && value.error ? value.error : undefined,
      addedAt: typeof value.addedAt === 'number' ? value.addedAt : 0,
    })
  }
  return items.sort((a, b) => a.addedAt - b.addedAt)
}

/** 真正需要落盘的部分：只保留未完成项，并把进度归零（重传不续传） */
export function persistableQueue(items: UploadQueueItem[]): UploadQueueItem[] {
  return items
    .filter((item) => item.status === 'pending' || item.status === 'error')
    .map((item) => ({
      uri: item.uri,
      status: item.status,
      progress: 0,
      error: item.error,
      addedAt: item.addedAt,
    }))
}

/** 读取恢复的队列（异常/空值一律返回空数组，不影响页面渲染） */
export async function loadUploadQueue(): Promise<UploadQueueItem[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    return normalizeRestoredQueue(JSON.parse(raw) as unknown)
  } catch {
    return []
  }
}

/** 保存队列（只写未完成项；写失败不影响上传主流程） */
export async function saveUploadQueue(items: UploadQueueItem[]): Promise<void> {
  const persistable = persistableQueue(items)
  try {
    if (persistable.length === 0) {
      await AsyncStorage.removeItem(STORAGE_KEY)
      return
    }
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(persistable))
  } catch {
    // 存储失败：本次会话仍可继续，重启后丢失（可接受，不打断上传）
  }
}

/** 清空持久化队列 */
export async function clearUploadQueue(): Promise<void> {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY)
  } catch {
    // 忽略
  }
}

/**
 * 探测本地图片是否还在：用已经在用的 `expo-image` 解码一次。
 *
 * 为什么不用 `expo-file-system`：为了「恢复时检查文件是否存在」再引入一个原生模块
 * 不划算（要重新 prebuild + 构建 APK）；`loadAsync` 命中文件缺失时会 reject，
 * 已经随 expo-image 一起打进包，能力等价。代价是要解码一次图片——放在恢复流程里
 * （最多 20 张）可以接受。
 */
export async function probeLocalFile(uri: string): Promise<boolean> {
  if (!uri) return false
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('probe timeout')), PROBE_TIMEOUT_MS)
    })
    await Promise.race([Image.loadAsync(uri), timeout])
    return true
  } catch {
    return false
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/**
 * 批量探测：把丢失文件的项标记为失败（保留在队列里让用户手动移除）。
 *
 * @returns 规整后的队列与丢失的 URI 列表
 */
export async function markMissingFiles(
  items: UploadQueueItem[],
): Promise<{ items: UploadQueueItem[]; missing: string[] }> {
  const missing: string[] = []
  const checked = await Promise.all(
    items.map(async (item) => {
      // 已经失败过的项不重复探测：错误原因可能是上次上传失败，与文件无关
      if (item.status === 'error' && item.error && item.error !== MISSING_FILE_REASON) return item
      const exists = await probeLocalFile(item.uri)
      if (exists) return item
      missing.push(item.uri)
      return { ...item, status: 'error' as UploadStatus, progress: 0, error: MISSING_FILE_REASON }
    }),
  )
  return { items: checked, missing }
}
