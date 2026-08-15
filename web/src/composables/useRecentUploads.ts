/** 最近上传 composable：sessionStorage 读取/写入最近上传记录。 */

import { ref } from 'vue'
import type { RecentUpload } from '@/types/upload'

/** 读取 sessionStorage 中的最近上传记录，解析失败时回退空数组（参考 ScraperView 的 try/catch 范式） */
function loadRecentUploads(): RecentUpload[] {
  try {
    const raw = sessionStorage.getItem('recent-uploads')
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function useRecentUploads() {
  const recentUploads = ref<RecentUpload[]>(loadRecentUploads())

  /** 把新上传记录插到最前，按 id 去重后最多保留 20 条 */
  function prependRecent(
    id: string,
    thumbnailPath: string | null,
    filePath: string,
    mediaType?: string,
  ) {
    recentUploads.value = [
      { id, thumbnailPath, filePath, mediaType },
      ...recentUploads.value.filter(r => r.id !== id),
    ].slice(0, 20)
    sessionStorage.setItem('recent-uploads', JSON.stringify(recentUploads.value))
  }

  return { recentUploads, prependRecent }
}
