/**
 * 模型管理面板 API 层（`useModelListApi`）的测试。
 *
 * 关注点：
 * - 模型名会出现在 URL 里，必须 `encodeURIComponent`（`qwen3-vl:8b` 的冒号、`/` 不能裸拼）；
 * - 「拉取/更新」下载地址由 baseURL + 模型名拼成，baseURL 缺省时回退 `/api`
 *   （否则拼出 `undefined/ai/models/...` 这种坏链接）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  defaults: { baseURL: '/api' },
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, defaults: mocks.defaults },
}))

import { useModelListApi } from '../useModelListApi'

describe('useModelListApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.restoreAllMocks()
    mocks.defaults.baseURL = '/api'
  })

  it('服务状态：GET /ai/status 并返回 data', async () => {
    mocks.get.mockResolvedValue({ data: { ollama_version: '0.5.1', ollama_uptime_seconds: 60 } })
    const api = useModelListApi()

    await expect(api.fetchStatus()).resolves.toEqual({
      ollama_version: '0.5.1',
      ollama_uptime_seconds: 60,
    })
    expect(mocks.get).toHaveBeenCalledWith('/ai/status')
  })

  it('模型详情：名称必须 URL 编码后再拼路径', async () => {
    mocks.get.mockResolvedValue({ data: { name: 'qwen3-vl:8b' } })
    const api = useModelListApi()

    await api.fetchDetail('qwen3-vl:8b')

    expect(mocks.get).toHaveBeenCalledWith('/ai/models/qwen3-vl%3A8b/detail')
    expect(mocks.get.mock.calls[0][0]).not.toContain(':')
  })

  it('复制模型：POST /ai/models/copy，空 body + params', async () => {
    mocks.post.mockResolvedValue({ data: { message: 'ok' } })
    const api = useModelListApi()

    await expect(api.copyModel('a:latest', 'b:latest')).resolves.toEqual({ message: 'ok' })
    expect(mocks.post).toHaveBeenCalledWith('/ai/models/copy', null, {
      params: { source: 'a:latest', destination: 'b:latest' },
    })
  })

  it('使用统计：GET /ai/model-stats', async () => {
    mocks.get.mockResolvedValue({ data: { models: [], total_analyses: 0 } })
    const api = useModelListApi()

    await expect(api.fetchStats()).resolves.toEqual({ models: [], total_analyses: 0 })
    expect(mocks.get).toHaveBeenCalledWith('/ai/model-stats')
  })

  it('下载地址：pull 走查询参数、update 走路径，模型名都编码', () => {
    const api = useModelListApi()

    expect(api.downloadUrl('qwen3-vl:8b', 'pull')).toBe(
      '/api/ai/models/pull?model_name=qwen3-vl%3A8b',
    )
    expect(api.downloadUrl('qwen3-vl:8b', 'update')).toBe('/api/ai/models/qwen3-vl%3A8b/update')
  })

  it('下载地址：baseURL 为空时回退 /api（不拼出 undefined）', () => {
    mocks.defaults.baseURL = ''
    const api = useModelListApi()

    expect(api.downloadUrl('m', 'pull')).toBe('/api/ai/models/pull?model_name=m')
    expect(api.downloadUrl('m', 'update')).toBe('/api/ai/models/m/update')
  })

  it('接口错误原样抛出（封装层不吞异常）', async () => {
    const err = new Error('503')
    mocks.get.mockRejectedValueOnce(err)
    const api = useModelListApi()

    await expect(api.fetchStatus()).rejects.toBe(err)
  })
})
