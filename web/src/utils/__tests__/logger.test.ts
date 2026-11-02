/** 前端日志缓冲（P2-4）：环形缓冲、request_id 注入诊断文本。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildDiagnostics, clearLogs, getRecentLogs, logger, setLastRequestId } from '../logger'

describe('前端日志缓冲', () => {
  beforeEach(() => {
    clearLogs()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'info').mockImplementation(() => {})
    vi.spyOn(console, 'debug').mockImplementation(() => {})
  })

  it('记录日志并按最近请求 request_id 关联', () => {
    setLastRequestId('req-abc123')
    logger.error('接口失败', { status: 500 })

    const logs = getRecentLogs()
    expect(logs).toHaveLength(1)
    expect(logs[0].level).toBe('error')
    expect(logs[0].message).toBe('接口失败')
    expect(logs[0].requestId).toBe('req-abc123')
    expect(logs[0].detail).toContain('500')
  })

  it('环形缓冲最多保留 200 条（丢最旧的）', () => {
    for (let index = 0; index < 205; index += 1) {
      logger.info(`第 ${index} 条`)
    }
    const logs = getRecentLogs()
    expect(logs).toHaveLength(200)
    expect(logs[0].message).toBe('第 5 条')
  })

  it('诊断文本包含环境信息、request_id 与日志', () => {
    setLastRequestId('req-diag')
    logger.warn('告警样例')
    const text = buildDiagnostics()
    expect(text).toContain('最近请求ID: req-diag')
    expect(text).toContain('req=req-diag')
    expect(text).toContain('告警样例')
  })
})
