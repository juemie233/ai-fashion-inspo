/** API 客户端：封装 Axios，管理后端地址（支持设置页自定义并持久化）。 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import axios from 'axios'
import { Platform } from 'react-native'

// Android 模拟器使用 10.0.2.2，iOS 模拟器用 localhost，真机用局域网 IP（设置页可改）
const DEFAULT_IP = Platform.select({
  android: '10.0.2.2',
  ios: 'localhost',
  default: 'localhost',
})

const DEFAULT_PORT = '18888'

/** 默认后端地址 */
const DEFAULT_BASE_URL = `http://${DEFAULT_IP}:${DEFAULT_PORT}`

/** 持久化存储键：自定义后端地址 */
const API_URL_STORAGE_KEY = 'fashion-inspo:api-base-url'

/** 当前 API 基础地址（模块级缓存；请求拦截器读取，设置页保存后更新） */
let currentApiBaseUrl = DEFAULT_BASE_URL

/** 去掉首尾空白与末尾斜杠，返回规范化地址；空串回退默认值 */
function normalizeBaseUrl(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, '')
  return trimmed || DEFAULT_BASE_URL
}

/** 初始化：从持久化存储恢复自定义后端地址（App 启动时调用一次） */
export async function loadApiBaseUrl(): Promise<string> {
  try {
    const saved = await AsyncStorage.getItem(API_URL_STORAGE_KEY)
    if (saved && saved.trim()) {
      currentApiBaseUrl = normalizeBaseUrl(saved)
    }
  } catch {
    // 读取失败（存储异常）静默使用默认地址
  }
  return currentApiBaseUrl
}

/** 保存自定义后端地址（设置页调用）；保存失败不影响本次会话（模块缓存已更新） */
export async function saveApiBaseUrl(url: string): Promise<void> {
  currentApiBaseUrl = normalizeBaseUrl(url)
  try {
    await AsyncStorage.setItem(API_URL_STORAGE_KEY, currentApiBaseUrl)
  } catch {
    // 持久化失败静默：本次运行仍生效，重启后回退
  }
}

/** 当前 API 基础地址（同步读取，供文件 URL 拼接与设置页展示） */
export function getApiBaseUrl(): string {
  return currentApiBaseUrl
}

/** 测试后端连通性：GET /api/health，返回是否可连（设置页「测试连接」用） */
export async function testApiConnection(baseUrl: string): Promise<{ ok: boolean; message: string }> {
  try {
    const normalized = normalizeBaseUrl(baseUrl)
    const resp = await axios.get(`${normalized}/api/health`, { timeout: 8000 })
    if (resp.status === 200) {
      return { ok: true, message: '连接成功' }
    }
    return { ok: false, message: `后端返回 HTTP ${resp.status}` }
  } catch (e) {
    return { ok: false, message: '无法连接，请检查地址与网络' }
  }
}

/** Axios 实例：baseURL 由请求拦截器按当前地址动态注入（支持运行中切换后端） */
export const apiClient = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  config.baseURL = `${currentApiBaseUrl}/api`
  return config
})

/** 拼接素材文件访问地址：按路径段 URL 编码，防止含空格/中文的路径 404 */
export function getFileUrl(base: string, relativePath: string): string {
  const encoded = relativePath.split('/').map(encodeURIComponent).join('/')
  return `${base}/api/files/${encoded}`
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
