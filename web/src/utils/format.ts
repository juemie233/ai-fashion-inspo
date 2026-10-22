/** 通用格式化工具函数。 */

import { h, type VNode } from 'vue'

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

/**
 * 紧凑时间：``YYYY-MM-DD HH:mm``（可选到秒），列表 / 卡片 / 详情里的时间统一走它。
 *
 * 为什么不复用 `formatDate`：`toLocaleString` 受系统 locale 与浏览器实现影响，
 * 同一份数据在不同机器上可能显示成 `2026/1/5 14:30:00`、`2026-01-05 14:30:00` 或带
 * 上午/下午，宽度还会变（时间列被挤到换行）。这里手工补零，输出**定宽可控**，
 * 也不依赖 locale；要「人类可读的完整时间」仍可用 `formatDate`。
 *
 * 之前有 4 处各写各的（`iso.slice(0, 16).replace('T', ' ')` / `slice(0, 19)` /
 * `new Date().toLocaleString()`），2026-10-20 代码审查统一到本函数。
 */
export function formatDateTime(d: string | Date | null | undefined, withSeconds = false): string {
  if (!d) return '-'
  const date = d instanceof Date ? d : new Date(d)
  if (isNaN(date.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  const base = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  return withSeconds ? `${base}:${pad(date.getSeconds())}` : base
}

/**
 * 表格时间列渲染辅助（项目规则：时间列必须单行显示，禁止换行）。
 * 接收已格式化的时间文本，包装为带 ``white-space: nowrap`` 的 span；
 * render 函数列统一用它，避免各列重复手写 nowrap 样式。
 * extra 可附加 span props（如自定义颜色/class），style 与 nowrap 合并。
 *
 * ⚠️ 仅用于表格列的 **render 函数**（返回 VNode）。在模板插值
 * ``{{ renderTimeCell(...) }}`` 中使用会把 VNode 当文本渲染，触发
 * 「Converting circular structure to JSON（vnode → component）」运行时错误；
 * 模板列请改用 ``<span style="white-space: nowrap">{{ formatDate(...) }}</span>``。
 */
export function renderTimeCell(
  text: string | null | undefined,
  extra: { style?: string | Record<string, string>; class?: string } = {},
): VNode {
  const style =
    typeof extra.style === 'string'
      ? `white-space: nowrap;${extra.style}`
      : { 'white-space': 'nowrap', ...(extra.style ?? {}) }
  return h('span', { ...extra, style }, text ?? '-')
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

/**
 * 档位显示名的短名：去掉括号里的说明。
 * 例：「Qwen-Image 2.1（参考图改款）」→「Qwen-Image 2.1」。
 * 出图页的档位单选与历史表都用它，避免两处各写一遍 split。
 */
export function shortModelLabel(label: string): string {
  return label.split('（')[0] ?? label
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

/** 安全解析 JSON：解析失败回退默认值（localStorage 偏好等容错读取统一入口）。 */
export function safeJsonParse<T>(raw: string | null | undefined, fallback: T): T {
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}
