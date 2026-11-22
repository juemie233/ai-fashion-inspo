/**
 * aiModels store：模型列表刷新与三种模型切换/删除的乐观更新语义。
 *
 * 这个 store 是「设置页 AI 模型」的共享状态，几个容易回归的点：
 * - 刷新失败只把连接状态置 false，不能清空已加载的列表；
 * - 并发刷新用请求序号防陈旧响应覆盖新数据（设置页会连点刷新）；
 * - 切换模型是乐观更新：PUT 失败要回滚；PUT 成功后刷新列表的失败被静默忽略
 *   （切换已生效，不能因为列表没刷新就回滚显示）；
 * - 删除模型名称要 URL 编码（模型名可能带 `:`、`/` 等字符）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, put: mocks.put, delete: mocks.delete },
}))

import { useAiModelsStore } from '../aiModels'

const MODELS_RESPONSE = {
  models: [
    {
      name: 'qwen3-vl:8b',
      size_bytes: 10,
      size_display: '10 GB',
      modified: '2026-11-01T00:00:00Z',
      is_active: true,
      is_embedding: false,
      vram_used: 8,
      loaded: true,
    },
  ],
  active_model: 'qwen3-vl:8b',
  embedding_model: 'bge-m3',
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: MODELS_RESPONSE })
  mocks.put.mockResolvedValue({ data: {} })
  mocks.delete.mockResolvedValue({ data: {} })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('aiModels store.refreshModels', () => {
  it('成功时填充模型列表/活跃模型/嵌入模型并把连接状态置 true', async () => {
    const store = useAiModelsStore()

    await store.refreshModels()

    expect(mocks.get).toHaveBeenCalledWith('/ai/models')
    expect(store.models).toHaveLength(1)
    expect(store.models[0]?.name).toBe('qwen3-vl:8b')
    expect(store.activeModel).toBe('qwen3-vl:8b')
    expect(store.embeddingModel).toBe('bge-m3')
    expect(store.ollamaConnected).toBe(true)
    expect(store.statusLoading).toBe(false)
  })

  it('失败时只置连接状态 false，不清空已有列表、不抛错', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.get.mockRejectedValueOnce(new Error('ollama down'))

    await expect(store.refreshModels()).resolves.toBeUndefined()

    expect(store.ollamaConnected).toBe(false)
    expect(store.statusLoading).toBe(false)
    // 旧列表保留，避免设置页突然空白
    expect(store.models).toHaveLength(1)
    expect(store.activeModel).toBe('qwen3-vl:8b')
  })

  it('越慢的旧请求不能覆盖新请求的结果（请求序号防抖）', async () => {
    const store = useAiModelsStore()
    let resolveOld!: (value: unknown) => void

    mocks.get
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          }),
      )
      .mockResolvedValueOnce({
        data: {
          models: [],
          active_model: '新模型',
          embedding_model: '新嵌入',
        },
      })

    const slow = store.refreshModels()
    await store.refreshModels()

    resolveOld({
      data: { models: [], active_model: '旧模型', embedding_model: '旧嵌入' },
    })
    await slow

    expect(store.activeModel).toBe('新模型')
    expect(store.embeddingModel).toBe('新嵌入')
    expect(store.statusLoading).toBe(false)
  })

  it('陈旧的失败响应不能把新请求后的连接状态误置为 false', async () => {
    const store = useAiModelsStore()
    let rejectOld!: (err: Error) => void

    mocks.get
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectOld = reject
          }),
      )
      .mockResolvedValueOnce({ data: MODELS_RESPONSE })

    const slow = store.refreshModels()
    await store.refreshModels() // 新请求先成功
    rejectOld(new Error('old request failed'))
    await slow

    // 旧请求的失败应被序号守卫忽略：连接状态仍是新请求的成功结果
    expect(store.ollamaConnected).toBe(true)
    expect(store.statusLoading).toBe(false)
  })
})

describe('aiModels store.setActiveModel', () => {
  it('成功：乐观更新后 PUT，再刷新列表，返回 true', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.get.mockClear()

    const ok = await store.setActiveModel('new-model')

    expect(ok).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith('/ai/models/active', null, {
      params: { model_name: 'new-model' },
    })
    expect(mocks.get).toHaveBeenCalledWith('/ai/models')
    expect(store.activeModel).toBe('qwen3-vl:8b') // 刷新后以服务端列表为准
  })

  it('PUT 失败：回滚旧值、返回 false，且不再刷新列表', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.put.mockRejectedValueOnce(new Error('500'))

    const ok = await store.setActiveModel('bad-model')

    expect(ok).toBe(false)
    expect(store.activeModel).toBe('qwen3-vl:8b') // 回滚
    expect(mocks.get).toHaveBeenCalledTimes(1) // 只有初始化那次
  })

  it('PUT 成功但刷新失败：返回 true 且不回滚（切换已生效）', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.get.mockRejectedValueOnce(new Error('刷新失败'))

    const ok = await store.setActiveModel('new-model')

    expect(ok).toBe(true)
    expect(store.activeModel).toBe('new-model')
    expect(store.ollamaConnected).toBe(false)
  })
})

describe('aiModels store.setEmbeddingModel', () => {
  it('成功：PUT /ai/models/embedding-active 并刷新', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()

    const ok = await store.setEmbeddingModel('new-embed')

    expect(ok).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith('/ai/models/embedding-active', null, {
      params: { model_name: 'new-embed' },
    })
    expect(mocks.get).toHaveBeenCalledWith('/ai/models')
  })

  it('PUT 失败：回滚嵌入模型、返回 false', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.put.mockRejectedValueOnce(new Error('500'))

    const ok = await store.setEmbeddingModel('bad-embed')

    expect(ok).toBe(false)
    expect(store.embeddingModel).toBe('bge-m3')
  })
})

describe('aiModels store.deleteModel', () => {
  it('成功：DELETE 编码后的模型名并刷新列表', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.get.mockClear()

    const ok = await store.deleteModel('qwen3-vl:8b/instruct #1')

    expect(ok).toBe(true)
    expect(mocks.delete).toHaveBeenCalledWith('/ai/models/qwen3-vl%3A8b%2Finstruct%20%231')
    expect(mocks.get).toHaveBeenCalledWith('/ai/models')
  })

  it('删除失败：返回 false 且不刷新列表', async () => {
    const store = useAiModelsStore()
    await store.refreshModels()
    mocks.get.mockClear()
    mocks.delete.mockRejectedValueOnce(new Error('文件被占用'))

    const ok = await store.deleteModel('qwen3-vl:8b')

    expect(ok).toBe(false)
    expect(mocks.get).not.toHaveBeenCalled()
  })
})
