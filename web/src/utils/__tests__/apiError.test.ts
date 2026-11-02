/** API 错误文案（P1-3）：后端 detail + X-Request-ID 拼接。 */

import { describe, expect, it } from 'vitest'
import { getApiErrorMessage } from '../apiError'

describe('getApiErrorMessage', () => {
  it('优先后端 detail，并在有 request_id 时附上', () => {
    const error = {
      response: {
        data: { detail: '任务不存在' },
        headers: { 'x-request-id': 'req-123456' },
      },
    }
    expect(getApiErrorMessage(error)).toBe('任务不存在（请求ID: req-123456）')
  })

  it('无响应头时保持原文案', () => {
    const error = { response: { data: { detail: '操作失败' }, headers: {} } }
    expect(getApiErrorMessage(error, '兜底')).toBe('操作失败')
  })

  it('非对象错误回退兜底文案', () => {
    expect(getApiErrorMessage(null, '兜底')).toBe('兜底')
  })
})
