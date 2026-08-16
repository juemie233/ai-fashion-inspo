/** 负样本初筛器 composable：状态查询、训练、重置（回滚纯 VLM）。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

export interface LearnerMetrics {
  accuracy: number
  precision: number
  recall: number
  f1: number
  false_reject_rate: number
  test_size: number
  confusion: { tn: number; fp: number; fn: number; tp: number }
}

export interface LearnerMeta {
  trained_at: string
  sample_total: number
  positive: number
  negative: number
  dim: number
  threshold: number
  metrics: LearnerMetrics
}

export interface LearnerDataset {
  positive_total?: number
  negative_total?: number
  with_vector?: number
  positive_with_vector?: number
  negative_with_vector?: number
  error?: string
}

export interface LearnerStatus {
  trained: boolean
  model_path: string
  threshold: number
  meta: LearnerMeta | null
  dataset: LearnerDataset | null
}

export function useQualityLearner() {
  const message = useMessage()
  const status = ref<LearnerStatus | null>(null)
  const loading = ref(false)
  const training = ref(false)
  const resetting = ref(false)

  /** 加载初筛器状态与正负样本统计 */
  async function loadStatus() {
    loading.value = true
    try {
      const { data } = await apiClient.get<LearnerStatus>('/ai/quality-learner/status')
      status.value = data
    } catch (e: any) {
      message.error(e.response?.data?.detail || '获取初筛器状态失败')
    } finally {
      loading.value = false
    }
  }

  /** 训练/重训初筛器，成功后刷新状态 */
  async function train() {
    training.value = true
    try {
      const { data } = await apiClient.post<{ error?: string }>('/ai/quality-learner/train')
      if (data.error) {
        message.warning(data.error)
        return
      }
      message.success('初筛器训练完成')
      await loadStatus()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '训练失败')
    } finally {
      training.value = false
    }
  }

  /** 删除已训练模型，回滚到纯 VLM 审核 */
  async function reset(): Promise<boolean> {
    resetting.value = true
    try {
      await apiClient.post('/ai/quality-learner/reset')
      message.success('已删除初筛器模型，回滚到纯 VLM 审核')
      await loadStatus()
      return true
    } catch (e: any) {
      message.error(e.response?.data?.detail || '重置失败（可能未配置 API Key）')
      return false
    } finally {
      resetting.value = false
    }
  }

  return { status, loading, training, resetting, loadStatus, train, reset }
}
