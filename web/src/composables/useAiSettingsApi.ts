/** 模型设置面板的 API 层：把 apiClient 调用从 SettingsPanel.vue 抽出。
 *
 * 组件仍负责表单状态与 Message 提示；本 composable 只做请求与响应类型收口，
 * 便于后续替换接口/单测时不用碰 700+ 行的设置面板。
 */

import apiClient from '@/api/client'

export interface AiSettings {
  active_model: string
  confidence_threshold: number
  analysis_timeout: number
  ollama_base_url: string
  defaults: { confidence_threshold: number; analysis_timeout: number }
}

export interface SamplingParams {
  temperature: number
  top_p: number
  top_k: number
  num_predict: number
  num_ctx: number
  think: boolean
  defaults: {
    temperature: number
    top_p: number
    top_k: number
    num_predict: number
    num_ctx: number
    think: boolean
  }
}

export interface PromptVersion {
  prompt: string
  saved_at: string
  length: number
}

export interface ModelConfigOverviewItem {
  name: string
  has_config: boolean
  config_fields: string[]
  has_prompt: boolean
  prompt_length: number
}

export interface PromptData {
  prompt: string
  length: number
}

export interface BioPromptData {
  prompt: string
  default: string
  length: number
}

export interface ResetData {
  message?: string
  snapshot?: string | null
}

export function useAiSettingsApi() {
  return {
    fetchPromptVersions: async () => {
      const { data } = await apiClient.get<{ versions: PromptVersion[]; current: string }>(
        '/ai/prompt/versions',
      )
      return data
    },
    updatePrompt: (prompt: string) => apiClient.put('/ai/prompt', { prompt }),
    savePromptVersion: () => apiClient.post('/ai/prompt/save-version'),
    rollbackPrompt: async (index: number) => {
      const { data } = await apiClient.post<{ prompt: string; message: string }>(
        '/ai/prompt/rollback',
        { index },
      )
      return data
    },
    resetAllData: async () => {
      const { data } = await apiClient.delete<ResetData>('/ai/reset', {
        params: { confirm: 'yes', confirm_text: 'DELETE' },
      })
      return data
    },
    fetchSettings: async () => {
      const { data } = await apiClient.get<AiSettings>('/ai/settings')
      return data
    },
    fetchSamplingParams: async () => {
      const { data } = await apiClient.get<SamplingParams>('/ai/sampling-params')
      return data
    },
    saveSettings: (confidenceThreshold: number, analysisTimeout: number) =>
      apiClient.put('/ai/settings', null, {
        params: {
          confidence_threshold: confidenceThreshold,
          analysis_timeout: analysisTimeout,
        },
      }),
    saveSamplingParams: (params: Omit<SamplingParams, 'defaults'>) =>
      apiClient.put('/ai/sampling-params', null, {
        params: {
          temperature: params.temperature,
          top_p: params.top_p,
          top_k: params.top_k,
          num_predict: params.num_predict,
          num_ctx: params.num_ctx,
          think: params.think,
        },
      }),
    clearModelConfig: async () => {
      const { data } = await apiClient.delete<{ message: string }>('/ai/model-config')
      return data
    },
    fetchConfigOverview: async () => {
      const { data } = await apiClient.get<{ models: ModelConfigOverviewItem[] }>(
        '/ai/model-config/overview',
      )
      return data
    },
    copyModelConfig: async (source: string, destination: string) => {
      const { data } = await apiClient.post<{ message: string }>('/ai/model-config/copy', {
        source,
        destination,
      })
      return data
    },
    fetchPrompt: async () => {
      const { data } = await apiClient.get<PromptData>('/ai/prompt')
      return data
    },
    fetchBioPrompt: async () => {
      const { data } = await apiClient.get<BioPromptData>('/ai/person-bio-prompt')
      return data
    },
    updateBioPrompt: (prompt: string) => apiClient.put('/ai/person-bio-prompt', { prompt }),
    resetBioPrompt: async () => {
      const { data } = await apiClient.delete<{ prompt: string }>('/ai/person-bio-prompt')
      return data
    },
  }
}
