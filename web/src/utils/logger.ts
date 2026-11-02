/**
 * 前端轻量日志：级别过滤 + 200 条环形缓冲 + 诊断信息导出。
 *
 * 背景（2026-11-02 日志系统全量扫描 P2-4）：Web 端只有零散 `console.*`，
 * 用户报「页面渲染出错 / 请求失败」时开发者拿不到浏览器控制台。这里不引入
 * 远程日志服务（素材与运行数据不出本机），只做三件事：
 *
 * 1. **级别**：生产构建只记 `warn` / `error`，`import.meta.env.DEV` 时全记；
 * 2. **环形缓冲**：最近 200 条留在内存，供错误边界/诊断按钮取用；
 * 3. **诊断文本**：`buildDiagnostics()` 把环境信息 + 最近日志 + request_id 拼成
 *    可复制文本，用来和 `logs/backend.log` 里的 `req=` 对齐（不新增后端接口）。
 */

/** 日志级别（内部类型；不需要对外导出，避免死代码检查噪音） */
type LogLevel = 'debug' | 'info' | 'warn' | 'error'

/** 缓冲区条目（内部类型） */
interface LogEntry {
  /** 本地时间（HH:mm:ss.SSS） */
  time: string
  level: LogLevel
  message: string
  /** 与后端日志对齐的请求 ID（来自 X-Request-ID 响应头） */
  requestId?: string
  /** 附加上下文（错误对象、状态码等），只保留可序列化摘要 */
  detail?: string
}

const MAX_ENTRIES = 200

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
}

/** 生产构建只保留 warn/error，避免把开发期噪音带进用户机器 */
const MIN_LEVEL: LogLevel = import.meta.env.DEV ? 'debug' : 'warn'

const entries: LogEntry[] = []

/** 最近一个 API 请求的 request_id（错误边界/诊断展示用） */
let lastRequestId = ''

function timestamp(): string {
  const now = new Date()
  const pad = (value: number, width = 2) => String(value).padStart(width, '0')
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(
    now.getMilliseconds(),
    3,
  )}`
}

/** 把任意 detail 压成一行可读文本（避免把 Error 的 [object Object] 带进日志） */
function stringifyDetail(detail: unknown): string | undefined {
  if (detail === undefined || detail === null) return undefined
  if (detail instanceof Error) {
    return detail.message ? `${detail.name}: ${detail.message}` : detail.name
  }
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail)
  } catch {
    return String(detail)
  }
}

function record(level: LogLevel, message: string, detail?: unknown): void {
  if (LEVEL_ORDER[level] < LEVEL_ORDER[MIN_LEVEL]) return
  const entry: LogEntry = {
    time: timestamp(),
    level,
    message,
    requestId: lastRequestId || undefined,
    detail: stringifyDetail(detail),
  }
  entries.push(entry)
  if (entries.length > MAX_ENTRIES) {
    entries.splice(0, entries.length - MAX_ENTRIES)
  }
  // 控制台仍保留一份：开发期与用户手动开控制台时可见
  const detailText = entry.detail ? ` ${entry.detail}` : ''
  const prefix = `[${entry.time}][${level.toUpperCase()}]`
  if (level === 'error') console.error(prefix, message + detailText)
  else if (level === 'warn') console.warn(prefix, message + detailText)
  else if (level === 'info') console.info(prefix, message + detailText)
  else console.debug(prefix, message + detailText)
}

/** 记录最近一次请求的 request_id（API 拦截器在收到响应头时调用） */
export function setLastRequestId(requestId: string | undefined): void {
  if (requestId) lastRequestId = requestId
}

/** 最近一次请求的 request_id（空串表示还没有请求失败过） */
export function getLastRequestId(): string {
  return lastRequestId
}

/** 取最近 N 条日志（默认全部，最多 200 条） */
export function getRecentLogs(limit = MAX_ENTRIES): LogEntry[] {
  return entries.slice(-limit)
}

/** 清空缓冲（测试用，正常不调用） */
export function clearLogs(): void {
  entries.length = 0
  lastRequestId = ''
}

/** 拼出可直接贴给开发者的诊断文本（环境 + 最近日志） */
export function buildDiagnostics(): string {
  const header = [
    `时间: ${new Date().toISOString()}`,
    `页面: ${typeof window !== 'undefined' ? window.location.href : '-'}`,
    `UA: ${typeof navigator !== 'undefined' ? navigator.userAgent : '-'}`,
    `最近请求ID: ${lastRequestId || '-'}`,
  ]
  const lines = getRecentLogs().map((entry) => {
    const requestId = entry.requestId ? ` req=${entry.requestId}` : ''
    const detail = entry.detail ? ` ${entry.detail}` : ''
    return `${entry.time} [${entry.level.toUpperCase()}]${requestId} ${entry.message}${detail}`
  })
  return [...header, '--- 最近日志 ---', ...lines].join('\n')
}

/** 复制诊断信息到剪贴板；失败返回 false（调用方负责提示用户手动复制） */
export async function copyDiagnostics(): Promise<boolean> {
  const text = buildDiagnostics()
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // 非 HTTPS / 无剪贴板权限时退回 textarea + execCommand
    try {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(area)
      return ok
    } catch {
      return false
    }
  }
}

/** 统一日志入口：页面与 store 一律走这里，便于缓冲与诊断导出 */
export const logger = {
  debug: (message: string, detail?: unknown) => record('debug', message, detail),
  info: (message: string, detail?: unknown) => record('info', message, detail),
  warn: (message: string, detail?: unknown) => record('warn', message, detail),
  error: (message: string, detail?: unknown) => record('error', message, detail),
}
