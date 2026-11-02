/** 移动端日志缓冲（P2-4）：环形缓冲与诊断文本。 */

import { buildDiagnostics, clearLogs, getRecentLogs, logger } from '../utils/logger'

describe('移动端日志缓冲', () => {
  beforeEach(() => {
    clearLogs()
    jest.spyOn(console, 'error').mockImplementation(() => {})
    jest.spyOn(console, 'warn').mockImplementation(() => {})
    jest.spyOn(console, 'info').mockImplementation(() => {})
    jest.spyOn(console, 'debug').mockImplementation(() => {})
  })

  it('记录日志并把错误压成一行 detail', () => {
    logger.error('接口失败', new Error('网络断开'))

    const logs = getRecentLogs()
    expect(logs).toHaveLength(1)
    expect(logs[0].level).toBe('error')
    expect(logs[0].detail).toContain('网络断开')
  })

  it('环形缓冲最多 200 条', () => {
    for (let index = 0; index < 205; index += 1) {
      logger.info(`第 ${index} 条`)
    }
    const logs = getRecentLogs()
    expect(logs).toHaveLength(200)
    expect(logs[0].message).toBe('第 5 条')
  })

  it('诊断文本带平台与最近日志', () => {
    logger.warn('告警样例')
    const text = buildDiagnostics()
    expect(text).toContain('平台:')
    expect(text).toContain('告警样例')
  })
})
