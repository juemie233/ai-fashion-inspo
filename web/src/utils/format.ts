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

/** 自适应大小格式化：数值保持在 1-1000 范围 + 单位 */
export function smartSize(bytes: number): { value: string; unit: string } {
  if (bytes < 1024) return { value: String(bytes), unit: 'B' }
  if (bytes < 1024 * 1024) return { value: (bytes / 1024).toFixed(1), unit: 'KB' }
  if (bytes < 1024 * 1024 * 1024) return { value: (bytes / (1024 * 1024)).toFixed(1), unit: 'MB' }
  return { value: (bytes / (1024 * 1024 * 1024)).toFixed(2), unit: 'GB' }
}

/** 返回 "数值 单位" 的完整字符串，如 "462.9 MB" */
export function fmtSize(bytes: number): string {
  const s = smartSize(bytes)
  return s.value + ' ' + s.unit
}

/** 格式化文件大小为可读字符串（KB 取整、MB 1 位、GB 2 位） */
export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB'
  return bytes + ' B'
}
