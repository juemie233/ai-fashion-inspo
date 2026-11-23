/**
 * 前端日志缓冲（logger）**补齐用例**：既有 `logger.test.ts` 覆盖了环形缓冲、
 * request_id 关联与诊断文本，本文件补其余易回归分支：
 *
 * - 级别过滤：生产构建（DEV=false）只记 warn/error，开发期全记；
 * - detail 序列化：Error → `Name: message`、无 message 的 Error → Name、字符串原样、
 *   普通对象 JSON、循环引用兜底 `String()`、null/undefined 不写入；
 * - 控制台路由与格式：error/warn/info/debug 各自走对应 console，前缀含时间与级别；
 * - getRecentLogs(limit) 取尾部、setLastRequestId 的空值不覆盖、clearLogs 复位；
 * - 诊断文本在无 request_id 时的占位；
 * - copyDiagnostics：Clipboard API 成功、被拒绝时 execCommand 回退、双失败返回 false。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  buildDiagnostics,
  clearLogs,
  copyDiagnostics,
  getLastRequestId,
  getRecentLogs,
  logger,
  setLastRequestId,
} from '../logger'

const writeText = vi.fn()

beforeEach(() => {
  clearLogs()
  vi.clearAllMocks()
  writeText.mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  vi.spyOn(console, 'info').mockImplementation(() => {})
  vi.spyOn(console, 'debug').mockImplementation(() => {})
})

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard')
  Reflect.deleteProperty(document, 'execCommand')
  document.body.innerHTML = ''
  vi.unstubAllEnvs()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('日志缓冲基础行为', () => {
  it('时间戳是 HH:mm:ss.SSS 定宽格式', () => {
    logger.info('时间格式')
    expect(getRecentLogs()[0]?.time).toMatch(/^\d{2}:\d{2}:\d{2}\.\d{3}$/)
  })

  it('getRecentLogs(limit) 取最近的 N 条，超量取全部', () => {
    logger.info('第一条')
    logger.info('第二条')
    logger.info('第三条')

    expect(getRecentLogs(2).map((entry) => entry.message)).toEqual(['第二条', '第三条'])
    expect(getRecentLogs(100)).toHaveLength(3)
    expect(getRecentLogs()).toHaveLength(3)
  })

  it('setLastRequestId 的空值不覆盖已有 request_id；clearLogs 复位', () => {
    setLastRequestId('req-1')
    setLastRequestId('')
    setLastRequestId(undefined)
    expect(getLastRequestId()).toBe('req-1')

    clearLogs()
    expect(getLastRequestId()).toBe('')
  })

  it('clearLogs 清空缓冲', () => {
    logger.error('待清空')
    clearLogs()
    expect(getRecentLogs()).toEqual([])
  })
})

describe('detail 序列化', () => {
  it('Error → Name: message；无 message 时只留 Name', () => {
    logger.error('带 message', new Error('boom'))
    logger.error('无 message', new DOMException('', 'AbortError'))

    const logs = getRecentLogs()
    expect(logs[0]?.detail).toBe('Error: boom')
    expect(logs[1]?.detail).toBe('AbortError')
  })

  it('字符串原样、对象 JSON、null/undefined 不写入 detail', () => {
    logger.info('字符串', 'ctx')
    logger.info('对象', { status: 500, url: '/x' })
    logger.info('null', null)
    logger.info('undefined', undefined)

    const logs = getRecentLogs()
    expect(logs[0]?.detail).toBe('ctx')
    expect(logs[1]?.detail).toBe('{"status":500,"url":"/x"}')
    expect(logs[2]?.detail).toBeUndefined()
    expect(logs[3]?.detail).toBeUndefined()
  })

  it('循环引用对象不会被 JSON.stringify 打挂，兜底 String(detail)', () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular

    logger.warn('循环', circular)

    expect(getRecentLogs()[0]?.detail).toBe('[object Object]')
  })
})

describe('控制台输出路由与格式', () => {
  it('四个级别分别走 console.error/warn/info/debug，前缀含级别，detail 拼在消息后', () => {
    logger.error('错误', { code: 1 })
    logger.warn('告警')
    logger.info('信息')
    logger.debug('调试')

    expect(console.error).toHaveBeenCalledWith(
      expect.stringMatching(/^\[\d{2}:\d{2}:\d{2}\.\d{3}\]\[ERROR\]$/),
      '错误 {"code":1}',
    )
    expect(console.warn).toHaveBeenCalledWith(
      expect.stringMatching(/^\[\d{2}:\d{2}:\d{2}\.\d{3}\]\[WARN\]$/),
      '告警',
    )
    expect(console.info).toHaveBeenCalledWith(expect.stringContaining('[INFO]'), '信息')
    expect(console.debug).toHaveBeenCalledWith(expect.stringContaining('[DEBUG]'), '调试')
  })

  it('request_id 会被写进日志条目（诊断时与后端 req= 对齐）', () => {
    setLastRequestId('req-xyz')
    logger.error('失败')

    expect(getRecentLogs()[0]?.requestId).toBe('req-xyz')
  })
})

describe('生产构建级别过滤（DEV=false）', () => {
  it('只保留 warn/error，debug/info 被丢弃', async () => {
    vi.stubEnv('DEV', false)
    vi.resetModules()
    const prod = await import('../logger')
    prod.clearLogs()

    prod.logger.debug('调试')
    prod.logger.info('信息')
    prod.logger.warn('告警')
    prod.logger.error('错误')

    expect(prod.getRecentLogs().map((entry) => entry.level)).toEqual(['warn', 'error'])
  })
})

describe('buildDiagnostics 诊断文本', () => {
  it('无 request_id 时占位「-」，日志行不含 req=；有 request_id 时两处都出现', () => {
    logger.warn('样例告警')
    const empty = buildDiagnostics()
    expect(empty).toContain('最近请求ID: -')
    expect(empty).toContain('--- 最近日志 ---')
    expect(empty).toContain('样例告警')
    expect(empty).not.toContain('req=')

    clearLogs()
    setLastRequestId('req-diag')
    logger.error('带请求 ID 的日志')
    const withId = buildDiagnostics()
    expect(withId).toContain('最近请求ID: req-diag')
    expect(withId).toContain('req=req-diag')
    expect(withId).toContain('页面: ')
    expect(withId).toContain('UA: ')
  })
})

describe('copyDiagnostics 复制诊断信息', () => {
  it('Clipboard API 成功：写入的是完整诊断文本，返回 true', async () => {
    setLastRequestId('req-copy')
    logger.warn('复制样例')

    await expect(copyDiagnostics()).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledTimes(1)
    const text = writeText.mock.calls[0]?.[0] as string
    expect(text).toContain('最近请求ID: req-copy')
    expect(text).toContain('复制样例')
  })

  it('Clipboard API 被拒绝：回退 execCommand，成功返回 true 且 textarea 用后移除', async () => {
    writeText.mockRejectedValue(new Error('NotAllowedError'))
    const execCommand = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand })

    await expect(copyDiagnostics()).resolves.toBe(true)

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('两条路都失败：返回 false（调用方提示手动复制）', async () => {
    writeText.mockRejectedValue(new Error('NotAllowedError'))
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: () => {
        throw new Error('execCommand 不可用')
      },
    })

    await expect(copyDiagnostics()).resolves.toBe(false)
  })
})
