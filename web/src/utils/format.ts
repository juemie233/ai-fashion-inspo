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

/** 将运行时长（秒）格式化为可读中文，如「2 小时 5 分钟」。 */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '-'
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const restMinutes = minutes % 60
  if (hours < 24) return restMinutes > 0 ? `${hours} 小时 ${restMinutes} 分钟` : `${hours} 小时`
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return restHours > 0 ? `${days} 天 ${restHours} 小时` : `${days} 天`
}

/** 规范化模型名：无 tag 时补 :latest，使 all-minilm 与 all-minilm:latest 等价。 */
export function normalizeModelName(name: string): string {
  if (!name) return name
  return name.includes(':') ? name : `${name}:latest`
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

/**
 * 精简长文本用于紧凑展示（如垃圾桶原因附加审核结论）：
 * 去除首尾空白，超长时按字符截断并加省略号，保留开头主要信息、不破坏原意。
 */
export function shortenText(text: string, maxLen = 16): string {
  const t = (text || '').trim()
  if (!t || t.length <= maxLen) return t
  return `${t.slice(0, maxLen)}…`
}
