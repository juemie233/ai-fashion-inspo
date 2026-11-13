/** 模型管理面板的 API 层：服务状态、详情、复制、使用统计与下载地址。 */

import apiClient from '@/api/client'

export interface ModelDetail {
  name: string
  parameter_size: string
  quantization_level: string
  family: string
  families: string[]
  format: string
  parent_model: string
  architecture: string
  template: string
  system: string
  license: string
  modelfile: string
  parameters: string
}

export interface ModelStat {
  model_name: string
  total_analyses: number
  success_count: number
  failure_count: number
  success_rate: number
  avg_time_ms: number
  avg_tags: number
  last_used: string
}

interface AiStatus {
  ollama_version: string
  ollama_uptime_seconds: number | null
}

export type DownloadEndpoint = 'pull' | 'update'

export function useModelListApi() {
  return {
    fetchStatus: async (): Promise<AiStatus> => {
      const { data } = await apiClient.get<AiStatus>('/ai/status')
      return data
    },
    fetchDetail: async (name: string): Promise<ModelDetail> => {
      const { data } = await apiClient.get<ModelDetail>(
        `/ai/models/${encodeURIComponent(name)}/detail`,
      )
      return data
    },
    copyModel: async (source: string, destination: string): Promise<{ message: string }> => {
      const { data } = await apiClient.post<{ message: string }>('/ai/models/copy', null, {
        params: { source, destination },
      })
      return data
    },
    fetchStats: async (): Promise<{ models: ModelStat[]; total_analyses: number }> => {
      const { data } = await apiClient.get<{ models: ModelStat[]; total_analyses: number }>(
        '/ai/model-stats',
      )
      return data
    },
    downloadUrl: (name: string, endpoint: DownloadEndpoint): string => {
      const baseUrl = apiClient.defaults.baseURL || '/api'
      return endpoint === 'update'
        ? `${baseUrl}/ai/models/${encodeURIComponent(name)}/update`
        : `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(name)}`
    },
  }
}
