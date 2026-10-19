/** 质量审核的「误判反馈」接口。 */

import apiClient from '@/api/client'

export interface QualityCorrectionPayload {
  inspiration_id: string
  /** 人工认为正确的判定 */
  corrected_status: 'approved' | 'rejected'
  /** 错在哪一类（复用 quality_reason_code 枚举），可选 */
  reason_code?: string
  /** 自由备注，可选 */
  note?: string
}

export interface QualityCorrectionResult {
  id: number
  inspiration_id: string
  original_status: string
  corrected_status: string
  reason_code: string | null
  note: string | null
}

/**
 * 提交一次「AI 判错了」的反馈。
 *
 * 后端会同时把素材状态改正并置 `quality_reviewed_by='human'`，
 * 因此这一次调用之后随机复审不会再覆盖它。
 */
export async function submitQualityCorrection(
  payload: QualityCorrectionPayload,
): Promise<QualityCorrectionResult> {
  const { data } = await apiClient.post<QualityCorrectionResult>('/ai/quality-corrections', payload)
  return data
}
