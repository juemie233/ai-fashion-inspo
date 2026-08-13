/** 通用格式化工具函数。 */

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(1)} ${units[i]}`
}

export function formatVram(bytes: number): string {
  if (!bytes || bytes === 0) return '-'
  return formatBytes(bytes)
}

export function formatMs(ms: number | null): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatDate(d: string | null | undefined): string {
  if (!d) return '-'
  try {
    const date = new Date(d)
    if (isNaN(date.getTime())) return '-'
    return date.toLocaleString('zh-CN')
  } catch {
    return '-'
  }
}
