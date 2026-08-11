/** Axios HTTP 客户端配置：基础 URL、超时、错误处理。 */

import axios from 'axios'
import { useMessage } from 'naive-ui'

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 响应拦截器：统一错误处理
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const msg = error.response?.data?.detail || error.message || '请求失败'
    console.error(`[API Error] ${msg}`)
    return Promise.reject(error)
  }
)

export default apiClient
