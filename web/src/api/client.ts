/** Axios HTTP 客户端配置：基础 URL、超时、API Key、错误处理。 */

import axios from 'axios'

/** 读取 API Key：优先 localStorage（用户手动设置），其次构建时环境变量 */
export function getApiKey(): string {
  try {
    return localStorage.getItem('apiKey') || import.meta.env.VITE_API_KEY || ''
  } catch {
    return import.meta.env.VITE_API_KEY || ''
  }
}

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
  // 仅 2xx 视为成功，4xx/5xx 进入 catch 分支触发错误处理
  validateStatus: (status) => status >= 200 && status < 300,
})

// 请求拦截器：配置了 API Key 时自动附加 X-API-Key 头（破坏性接口认证用）
apiClient.interceptors.request.use((config) => {
  const apiKey = getApiKey()
  if (apiKey) {
    config.headers = config.headers || {}
    config.headers['X-API-Key'] = apiKey
  }
  return config
})

// 响应拦截器：统一错误处理
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const msg = error.response?.data?.detail || error.message || '请求失败'
    if (status === 401 || status === 403) {
      console.warn(
        `[API 认证] ${msg} —— 若已启用 API 密钥，请在控制台执行 ` +
          `localStorage.setItem('apiKey', '<密钥>') 后刷新（生成方式见 scripts/generate_api_key.py）`
      )
    }
    console.error(`[API Error] ${msg}`)
    return Promise.reject(error)
  }
)

export default apiClient
