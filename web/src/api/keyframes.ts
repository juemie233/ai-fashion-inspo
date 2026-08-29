/** 视频关键帧 API 客户端（详情页关键帧缩略图展示）。 */

import apiClient from './client'

/** 关键帧列表响应（非视频素材 frames 恒为空数组） */
export interface KeyframesOut {
  inspiration_id: string
  media_type: string
  count: number
  /** 帧图片 URL 列表（/api/files/keyframes/{id}/frame_001.jpg，按时间序） */
  frames: string[]
}

/** 获取素材关键帧 URL 列表（视频素材首次访问时后端懒提取，可能稍慢） */
export async function fetchKeyframes(inspirationId: string): Promise<KeyframesOut> {
  const { data } = await apiClient.get<KeyframesOut>(`/files/keyframes/${inspirationId}`)
  return data
}
