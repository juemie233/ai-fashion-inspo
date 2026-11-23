/**
 * Axios 客户端（api/client.ts）：基础配置、API Key 注入与统一错误拦截。
 *
 * 这个文件没有页面级测试，但它是**所有请求的入口**：
 * - baseURL/timeout/Content-Type 决定请求发去哪、多久超时；
 * - `validateStatus` 只把 2xx 当成功，4xx/5xx 必须进 catch 由调用方提示；
 * - 请求拦截器在配置了 API Key 时注入 X-API-Key（破坏性接口认证）；
 * - 响应拦截器统一记录 [API Error]、识别 401/403 给出密钥设置指引，
 *   并把 X-Request-ID 存进 logger 供用户报障时对齐后端日志。
 *
 * 这里 mock axios（不触网）：捕获 create 的配置与两个拦截器回调，直接调用验证行为。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const requestUse = vi.fn()
  const responseUse = vi.fn()
  const instance = {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
  }
  return {
    create: vi.fn(() => instance),
    instance,
    requestUse,
    responseUse,
    warn: vi.fn(),
    error: vi.fn(),
    setLastRequestId: vi.fn(),
  }
})

vi.mock('axios', () => ({ default: { create: mocks.create } }))
vi.mock('@/utils/logger', () => ({
  logger: { warn: mocks.warn, error: mocks.error },
  setLastRequestId: mocks.setLastRequestId,
}))

import apiClient, { getApiKey } from '../client'

interface RequestConfig {
  headers?: Record<string, string>
}

interface AxiosLikeError {
  message?: string
  config?: { url?: string }
  response?: {
    status?: number
    data?: unknown
    headers?: Record<string, string>
  }
}

// 模块求值时就已调用 create/use；在 beforeEach 的 clearAllMocks 之前捕获
const createConfig = (mocks.create.mock.calls as unknown[][])[0]?.[0] as {
  baseURL: string
  timeout: number
  headers: Record<string, string>
  validateStatus: (status: number) => boolean
}
const requestHandler = mocks.requestUse.mock.calls[0]?.[0] as (
  config: RequestConfig,
) => RequestConfig
const [onFulfilled, onRejected] = mocks.responseUse.mock.calls[0] as [
  (response: unknown) => unknown,
  (error: AxiosLikeError) => Promise<never>,
]

function axiosError(over: {
  status?: number
  detail?: unknown
  message?: string
  requestId?: string | null
}): AxiosLikeError {
  const headers: Record<string, string> = {}
  if (over.requestId) headers['x-request-id'] = over.requestId
  return {
    message: over.message ?? 'Request failed with status code 500',
    config: { url: '/admin/stats' },
    response: {
      status: over.status ?? 500,
      data: over.detail === undefined ? { detail: '服务端错误' } : { detail: over.detail },
      headers,
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('基础配置', () => {
  it('baseURL=/api、30s 超时、JSON Content-Type', () => {
    expect(apiClient).toBe(mocks.instance)
    expect(createConfig).toMatchObject({
      baseURL: '/api',
      timeout: 30000,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  it('validateStatus 只把 2xx 当成功（4xx/5xx 必须进 catch 触发错误处理）', () => {
    expect(createConfig.validateStatus(200)).toBe(true)
    expect(createConfig.validateStatus(299)).toBe(true)
    expect(createConfig.validateStatus(199)).toBe(false)
    expect(createConfig.validateStatus(300)).toBe(false)
    expect(createConfig.validateStatus(401)).toBe(false)
    expect(createConfig.validateStatus(500)).toBe(false)
  })
})

describe('getApiKey 读取顺序', () => {
  it('localStorage 的 apiKey 优先于构建时环境变量', () => {
    vi.stubEnv('VITE_API_KEY', 'env-key')
    localStorage.setItem('apiKey', 'local-key')

    expect(getApiKey()).toBe('local-key')
  })

  it('localStorage 没有时回退 VITE_API_KEY', () => {
    vi.stubEnv('VITE_API_KEY', 'env-key')

    expect(getApiKey()).toBe('env-key')
  })

  it('两者都没有时返回空串', () => {
    expect(getApiKey()).toBe('')
  })

  it('localStorage 访问抛错（隐私模式/被禁用）时静默回退环境变量', () => {
    vi.stubEnv('VITE_API_KEY', 'env-key')
    // 直接替换全局 localStorage：模拟浏览器隐私模式/被禁用时访问即抛错
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError')
      },
    })

    expect(getApiKey()).toBe('env-key')

    // 环境变量也没有时回退空串（不抛错）
    vi.stubEnv('VITE_API_KEY', '')
    expect(getApiKey()).toBe('')
  })
})

describe('请求拦截器：X-API-Key 注入', () => {
  it('有 API Key 时写入 X-API-Key 头', () => {
    localStorage.setItem('apiKey', 'secret-key')
    const config: RequestConfig = { headers: {} }

    const result = requestHandler(config)

    expect(result.headers?.['X-API-Key']).toBe('secret-key')
  })

  it('没有 API Key 时不写头，原样返回 config', () => {
    const config: RequestConfig = { headers: {} }

    const result = requestHandler(config)

    expect(result).toBe(config)
    expect(result.headers?.['X-API-Key']).toBeUndefined()
  })

  it('config 没有 headers 字段时会补出来（避免 TypeError）', () => {
    localStorage.setItem('apiKey', 'secret-key')

    const result = requestHandler({})

    expect(result.headers?.['X-API-Key']).toBe('secret-key')
  })
})

describe('响应拦截器：成功直通', () => {
  it('2xx 响应原样返回给调用方', () => {
    const response = { data: { ok: true }, status: 200 }

    expect(onFulfilled(response)).toBe(response)
  })
})

describe('响应拦截器：错误统一处理', () => {
  it('5xx：记录 [API Error] + 上下文，且原样 reject（不吞错）', async () => {
    const err = axiosError({ status: 500, detail: '数据库写入失败', requestId: 'rid-500' })

    await expect(onRejected(err)).rejects.toBe(err)

    expect(mocks.setLastRequestId).toHaveBeenCalledWith('rid-500')
    expect(mocks.error).toHaveBeenCalledWith('[API Error] 数据库写入失败', {
      status: 500,
      url: '/admin/stats',
      requestId: 'rid-500',
    })
    // 只有 401/403 才额外给密钥设置指引
    expect(mocks.warn).not.toHaveBeenCalled()
  })

  it('401：附加「API 认证」告警，提示如何设置密钥', async () => {
    const err = axiosError({ status: 401, detail: '未授权', requestId: 'rid-401' })

    await expect(onRejected(err)).rejects.toBe(err)

    expect(mocks.warn).toHaveBeenCalledWith(expect.stringContaining('[API 认证] 未授权'), {
      status: 401,
      url: '/admin/stats',
      requestId: 'rid-401',
    })
    expect(mocks.error).toHaveBeenCalledWith('[API Error] 未授权', expect.any(Object))
  })

  it('403：同样走认证告警分支', async () => {
    const err = axiosError({ status: 403, detail: '无权限' })

    await expect(onRejected(err)).rejects.toBe(err)

    expect(mocks.warn).toHaveBeenCalledWith(
      expect.stringContaining('[API 认证] 无权限'),
      expect.any(Object),
    )
  })

  it('后端 detail 是数组（FastAPI 422）时日志退化为 [object Object]；用户提示由 apiError 兜底', async () => {
    const err = axiosError({
      status: 422,
      detail: [{ loc: ['body', 'name'], msg: 'field required' }],
      message: 'Validation failed',
    })

    await expect(onRejected(err)).rejects.toBe(err)

    // 拦截器直接用 `${detail}` 拼日志（数组会变成 [object Object]）；
    // 用户可见文案走 utils/apiError.ts，那里会跳过非字符串 detail 回退 error.message
    expect(mocks.error).toHaveBeenCalledWith('[API Error] [object Object]', expect.any(Object))
  })

  it('既无 response.detail 也无 message 时回退默认文案「请求失败」', async () => {
    const err: AxiosLikeError = {
      config: { url: '/admin/stats' },
      response: { status: 0, data: {}, headers: {} },
    }

    await expect(onRejected(err)).rejects.toBe(err)

    expect(mocks.error).toHaveBeenCalledWith('[API Error] 请求失败', {
      status: 0,
      url: '/admin/stats',
      requestId: undefined,
    })
  })

  it('响应没有 request_id 时不写 logger 的 request_id，上下文里为 undefined', async () => {
    const err = axiosError({ status: 500, detail: '内部错误', requestId: null })

    await expect(onRejected(err)).rejects.toBe(err)

    expect(mocks.setLastRequestId).not.toHaveBeenCalled()
    expect(mocks.error).toHaveBeenCalledWith('[API Error] 内部错误', {
      status: 500,
      url: '/admin/stats',
      requestId: undefined,
    })
  })
})
