/** AI 模型共享状态：模型列表、活跃模型、连接状态，供各 tab 面板复用。 */

import { ref } from 'vue'
import { defineStore } from 'pinia'
import apiClient from '@/api/client'

export interface OllamaModel {
  name: string
  size_bytes: number
  size_display: string
  modified: string
  is_active: boolean
  vram_used: number
  loaded: boolean
}

interface ModelListResponse {
  models: OllamaModel[]
  active_model: string
}

export const useAiModelsStore = defineStore('aiModels', () => {
  const models = ref<OllamaModel[]>([])
  const activeModel = ref('')
  const ollamaConnected = ref(false)
  const statusLoading = ref(false)

  /** 刷新模型列表与活跃模型 */
  let refreshSeq = 0  // 请求序号，防止并发刷新时陈旧响应覆盖新数据
  async function refreshModels() {
    statusLoading.value = true
    const seq = ++refreshSeq
    try {
      const { data } = await apiClient.get<ModelListResponse>('/ai/models')
      if (seq !== refreshSeq) return
      models.value = data.models
      activeModel.value = data.active_model
      ollamaConnected.value = true
    } catch {
      if (seq !== refreshSeq) return
      ollamaConnected.value = false
    } finally {
      if (seq === refreshSeq) statusLoading.value = false
    }
  }

  /** 切换活跃模型，返回是否成功（成功时已刷新列表） */
  async function setActiveModel(name: string): Promise<boolean> {
    try {
      await apiClient.put('/ai/models/active', null, { params: { model_name: name } })
      activeModel.value = name
      await refreshModels()
      return true
    } catch {
      return false
    }
  }

  /** 删除模型，返回是否成功 */
  async function deleteModel(name: string): Promise<boolean> {
    try {
      await apiClient.delete(`/ai/models/${encodeURIComponent(name)}`)
      await refreshModels()
      return true
    } catch {
      return false
    }
  }

  return { models, activeModel, ollamaConnected, statusLoading, refreshModels, setActiveModel, deleteModel }
})
