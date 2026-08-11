/** API 客户端：封装 Axios，管理后端地址。 */

import axios from 'axios'
import { Platform } from 'react-native'

// Android 模拟器使用 10.0.2.2，iOS 模拟器用 localhost，真机用局域网 IP
const DEFAULT_IP = Platform.select({
  android: '10.0.2.2',
  ios: 'localhost',
  default: 'localhost',
})

const DEFAULT_PORT = '8000'

/** 获取 API 基础地址 */
export function getApiBaseUrl(): string {
  return `http://${DEFAULT_IP}:${DEFAULT_PORT}`
}

/** Axios 实例 */
export const apiClient = axios.create({
  baseURL: `${getApiBaseUrl()}/api`,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

/** 更新后端地址（用户可在设置中修改） */
export function setApiBaseUrl(ip: string, port: string = '8000') {
  apiClient.defaults.baseURL = `http://${ip}:${port}/api`
}

// 类型定义
export interface Inspiration {
  id: string
  source_type: string
  source_url?: string | null
  source_author?: string | null
  source_platform_id?: string | null
  file_path: string
  thumbnail_path?: string | null
  media_type: string
  dominant_colors?: string | null
  is_favorite: boolean
  created_at: string
  updated_at?: string | null
  tags: InspirationTag[]
  analysis_status?: string | null
}

export interface InspirationTag {
  tag: Tag
  confidence: number
}

export interface Tag {
  id: number
  name: string
  category: string
}

export interface InspirationListResponse {
  items: Inspiration[]
  total: number
  page: number
  size: number
}

export interface TagCategoryGroup {
  category: string
  tags: (Tag & { usage_count: number })[]
}
