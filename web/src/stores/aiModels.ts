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
  is_embedding: boolean
  vram_used: number
  loaded: boolean
}

interface ModelListResponse {
  models: OllamaModel[]
  active_model: string
  embedding_model: string
}

export const useAiModelsStore = defineStore('aiModels', () => {
  const models = ref<OllamaModel[]>([])
  const activeModel = ref('')
  const embeddingModel = ref('')
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
      embeddingModel.value = data.embedding_model
      ollamaConnected.value = true
    } catch {
      if (seq !== refreshSeq) return
      ollamaConnected.value = false
    } finally {
      if (seq === refreshSeq) statusLoading.value = false
    }
  }

  /** 切换活跃视觉模型，返回是否成功（成功时已刷新列表） */
  async function setActiveModel(name: string): Promise<boolean> {
    const previous = activeModel.value  // 记录旧值，仅 PUT 失败时回滚
    activeModel.value = name  // 乐观赋值：界面立即切换
    try {
      await apiClient.put('/ai/models/active', null, { params: { model_name: name } })
    } catch {
      activeModel.value = previous  // 仅 PUT 本身失败才回滚，避免界面显示已切换但实际未生效
      return false
    }
    // PUT 已成功：刷新列表；refreshModels 失败单独静默降级，不回滚已生效的切换
    try {
      await refreshModels()
    } catch {
      // 刷新失败静默忽略：列表可能暂时不新，但活跃模型切换已成功
    }
    return true
  }

  /** 切换文本嵌入模型，返回是否成功 */
  async function setEmbeddingModel(name: string): Promise<boolean> {
    const previous = embeddingModel.value
    embeddingModel.value = name  // 乐观赋值，失败回滚
    try {
      await apiClient.put('/ai/models/embedding-active', null, { params: { model_name: name } })
    } catch {
      embeddingModel.value = previous
      return false
    }
    try {
      await refreshModels()
    } catch {
      // 刷新失败静默忽略：切换已生效
    }
    return true
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

  return {
    models,
    activeModel,
    embeddingModel,
    ollamaConnected,
    statusLoading,
    refreshModels,
    setActiveModel,
    setEmbeddingModel,
    deleteModel,
  }
})
