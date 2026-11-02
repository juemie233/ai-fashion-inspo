/**
 * 移动端轻量日志：__DEV__ 级别控制 + 200 条环形缓冲 + 诊断文本（2026-11-02 日志扫描 P2-4）。
 *
 * 背景：生产 APK 里 `console.*` 用户看不到，出问题只能靠截图错误页；不引入
 * Sentry 等外部依赖（素材与运行数据不出本机）。这里只做：
 *
 * 1. **级别**：`__DEV__` 时全记，release 只记 `warn` / `error`；
 * 2. **环形缓冲**：最近 200 条留内存，错误边界据此生成「分享诊断信息」；
 * 3. **诊断文本**：`buildDiagnostics()` 拼出平台/版本 + 最近日志，用户可直接
 *    通过系统分享面板发给开发者。
 */

import { Platform } from 'react-native'

export type LogLevel = 'debug' | 'info' | 'warn' | 'error'

export interface LogEntry {
  /** 本地时间（HH:mm:ss.SSS） */
  time: string
  level: LogLevel
  message: string
  /** 附加上下文（错误对象会压成一行文本） */
  detail?: string
}

const MAX_ENTRIES = 200

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
}

/** release 包只保留 warn/error，避免开发期噪音留在用户机器上 */
const MIN_LEVEL: LogLevel = __DEV__ ? 'debug' : 'warn'

const entries: LogEntry[] = []

function timestamp(): string {
  const now = new Date()
  const pad = (value: number, width = 2) => String(value).padStart(width, '0')
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(
    now.getMilliseconds(),
    3,
  )}`
}

/** 把任意 detail 压成一行文本（避免把 [object Object] 写进诊断信息） */
function stringifyDetail(detail: unknown): string | undefined {
  if (detail === undefined || detail === null) return undefined
  if (detail instanceof Error) {
    return detail.stack ? `${detail.name}: ${detail.message}\n${detail.stack}` : detail.message
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
    detail: stringifyDetail(detail),
  }
  entries.push(entry)
  if (entries.length > MAX_ENTRIES) {
    entries.splice(0, entries.length - MAX_ENTRIES)
  }
  // 控制台仍留一份：`adb logcat` / `expo start` 时可见
  const detailText = entry.detail ? ` ${entry.detail}` : ''
  const prefix = `[${entry.time}][${level.toUpperCase()}]`
  if (level === 'error') console.error(prefix, message + detailText)
  else if (level === 'warn') console.warn(prefix, message + detailText)
  else if (level === 'info') console.info(prefix, message + detailText)
  else console.debug(prefix, message + detailText)
}

/** 取最近 N 条日志（默认全部，最多 200 条） */
export function getRecentLogs(limit = MAX_ENTRIES): LogEntry[] {
  return entries.slice(-limit)
}

/** 清空缓冲（测试用） */
export function clearLogs(): void {
  entries.length = 0
}

/** 拼出可分享给开发者的诊断文本（平台 + 最近日志） */
export function buildDiagnostics(): string {
  const header = [
    `时间: ${new Date().toISOString()}`,
    `平台: ${Platform.OS} ${String(Platform.Version)}`,
  ]
  const lines = getRecentLogs().map((entry) => {
    const detail = entry.detail ? ` ${entry.detail}` : ''
    return `${entry.time} [${entry.level.toUpperCase()}] ${entry.message}${detail}`
  })
  return [...header, '--- 最近日志 ---', ...lines].join('\n')
}

/** 统一日志入口：页面 / hooks 一律走这里，便于缓冲与诊断导出 */
export const logger = {
  debug: (message: string, detail?: unknown) => record('debug', message, detail),
  info: (message: string, detail?: unknown) => record('info', message, detail),
  warn: (message: string, detail?: unknown) => record('warn', message, detail),
  error: (message: string, detail?: unknown) => record('error', message, detail),
}

export default logger
