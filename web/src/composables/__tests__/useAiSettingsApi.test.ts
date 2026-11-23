/**
 * 模型设置面板 API 层（useAiSettingsApi）：把 SettingsPanel.vue 的请求收口。
 *
 * 这里每个方法都对应一个后端端点：提示词版本/回滚、AI 参数（分别走 params 与 body）、
 * 重置数据（必须带 confirm=yes + confirm_text=DELETE 双重确认）、模型配置复制等。
 * 契约错一处设置页就 422/404，且大多没有页面级测试兜底，所以在封装层逐条锁死。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    put: mocks.put,
    delete: mocks.delete,
  },
}))

import { useAiSettingsApi } from '../useAiSettingsApi'

function lastArgs(mockFn: { mock: { calls: unknown[][] } }): unknown[] {
  return mockFn.mock.calls.at(-1) ?? []
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: {} })
  mocks.post.mockResolvedValue({ data: {} })
  mocks.put.mockResolvedValue({ data: {} })
  mocks.delete.mockResolvedValue({ data: {} })
})

describe('提示词版本', () => {
  it('版本列表 / 更新 / 保存版本 / 回滚', async () => {
    const api = useAiSettingsApi()

    mocks.get.mockResolvedValueOnce({ data: { versions: [], current: 'abc' } })
    await expect(api.fetchPromptVersions()).resolves.toEqual({ versions: [], current: 'abc' })
    expect(mocks.get).toHaveBeenCalledWith('/ai/prompt/versions')

    await api.updatePrompt('新的分析提示词')
    expect(mocks.put).toHaveBeenLastCalledWith('/ai/prompt', { prompt: '新的分析提示词' })

    await api.savePromptVersion()
    expect(lastArgs(mocks.post)).toEqual(['/ai/prompt/save-version'])

    mocks.post.mockResolvedValueOnce({ data: { prompt: '旧版', message: '已回滚' } })
    await expect(api.rollbackPrompt(2)).resolves.toEqual({ prompt: '旧版', message: '已回滚' })
    expect(mocks.post).toHaveBeenLastCalledWith('/ai/prompt/rollback', { index: 2 })
  })
})

describe('AI 参数设置', () => {
  it('读取设置与采样参数：GET 对应端点并解包 data', async () => {
    const api = useAiSettingsApi()
    const settings = {
      active_model: 'qwen3-vl:8b',
      confidence_threshold: 0.7,
      analysis_timeout: 120,
      ollama_base_url: 'http://127.0.0.1:11434',
      defaults: { confidence_threshold: 0.6, analysis_timeout: 90 },
    }
    mocks.get.mockResolvedValueOnce({ data: settings })
    await expect(api.fetchSettings()).resolves.toBe(settings)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/settings')

    const sampling = {
      temperature: 0.7,
      top_p: 0.9,
      top_k: 40,
      num_predict: 2048,
      num_ctx: 8192,
      think: true,
      defaults: {
        temperature: 0.8,
        top_p: 0.95,
        top_k: 40,
        num_predict: 1024,
        num_ctx: 4096,
        think: false,
      },
    }
    mocks.get.mockResolvedValueOnce({ data: sampling })
    await expect(api.fetchSamplingParams()).resolves.toBe(sampling)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/sampling-params')
  })

  it('保存设置：PUT 无 body，两个值放 params', async () => {
    const api = useAiSettingsApi()

    await api.saveSettings(0.75, 150)

    expect(mocks.put).toHaveBeenCalledWith('/ai/settings', null, {
      params: { confidence_threshold: 0.75, analysis_timeout: 150 },
    })
  })

  it('保存采样参数：只透传六项可调参数（defaults 由后端维护，不发给后端）', async () => {
    const api = useAiSettingsApi()

    await api.saveSamplingParams({
      temperature: 0.6,
      top_p: 0.8,
      top_k: 30,
      num_predict: 512,
      num_ctx: 2048,
      think: false,
    })

    expect(mocks.put).toHaveBeenCalledWith('/ai/sampling-params', null, {
      params: {
        temperature: 0.6,
        top_p: 0.8,
        top_k: 30,
        num_predict: 512,
        num_ctx: 2048,
        think: false,
      },
    })
  })
})

describe('数据重置与模型配置', () => {
  it('重置全部 AI 数据：DELETE /ai/reset，双重确认参数必须带齐', async () => {
    const api = useAiSettingsApi()
    mocks.delete.mockResolvedValueOnce({ data: { message: '已重置', snapshot: 'backup.zip' } })

    await expect(api.resetAllData()).resolves.toEqual({
      message: '已重置',
      snapshot: 'backup.zip',
    })
    expect(mocks.delete).toHaveBeenCalledWith('/ai/reset', {
      params: { confirm: 'yes', confirm_text: 'DELETE' },
    })
  })

  it('模型配置：清空 / 总览 / 复制（复制 body 带 source+destination）', async () => {
    const api = useAiSettingsApi()

    mocks.delete.mockResolvedValueOnce({ data: { message: '已清空' } })
    await expect(api.clearModelConfig()).resolves.toEqual({ message: '已清空' })
    expect(mocks.delete).toHaveBeenLastCalledWith('/ai/model-config')

    const overview = { models: [{ name: 'qwen3-vl:8b', has_config: true }] }
    mocks.get.mockResolvedValueOnce({ data: overview })
    await expect(api.fetchConfigOverview()).resolves.toBe(overview)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/model-config/overview')

    mocks.post.mockResolvedValueOnce({ data: { message: '已复制' } })
    await expect(api.copyModelConfig('qwen3-vl:8b', 'qwen3-vl:8b-copy')).resolves.toEqual({
      message: '已复制',
    })
    expect(mocks.post).toHaveBeenLastCalledWith('/ai/model-config/copy', {
      source: 'qwen3-vl:8b',
      destination: 'qwen3-vl:8b-copy',
    })
  })
})

describe('人物简介提示词', () => {
  it('读取 / 更新 / 恢复默认：三条端点', async () => {
    const api = useAiSettingsApi()

    const promptData = { prompt: '当前提示词', length: 5 }
    mocks.get.mockResolvedValueOnce({ data: promptData })
    await expect(api.fetchPrompt()).resolves.toBe(promptData)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/prompt')

    const bio = { prompt: '简介提示词', default: '默认提示词', length: 6 }
    mocks.get.mockResolvedValueOnce({ data: bio })
    await expect(api.fetchBioPrompt()).resolves.toBe(bio)
    expect(mocks.get).toHaveBeenLastCalledWith('/ai/person-bio-prompt')

    await api.updateBioPrompt('新简介提示词')
    expect(mocks.put).toHaveBeenLastCalledWith('/ai/person-bio-prompt', {
      prompt: '新简介提示词',
    })

    mocks.delete.mockResolvedValueOnce({ data: { prompt: '默认提示词' } })
    await expect(api.resetBioPrompt()).resolves.toEqual({ prompt: '默认提示词' })
    expect(mocks.delete).toHaveBeenLastCalledWith('/ai/person-bio-prompt')
  })
})

describe('错误透传', () => {
  it('请求失败原样 reject（面板自己提示）', async () => {
    mocks.get.mockRejectedValueOnce(new Error('settings 500'))
    const api = useAiSettingsApi()

    await expect(api.fetchSettings()).rejects.toThrow('settings 500')
  })
})
