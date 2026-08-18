/** API 错误文案提取：统一从 axios 错误中取后端 detail，消除各处的重复解析。 */

/**
 * 从未知错误中提取可展示的文案（优先后端 detail，其次 error.message）。
 *
 * 后端错误响应为 `{ "detail": "..." }`；FastAPI 校验错误 detail 为数组，
 * 此时回退到 error.message / fallback，避免展示序列化数组。
 *
 * @param e 捕获到的异常（axios error / 任意 throw）
 * @param fallback 无法提取时的兜底文案
 * @returns 展示用错误信息
 */
export function getApiErrorMessage(e: unknown, fallback = '操作失败'): string {
  if (e && typeof e === 'object') {
    const err = e as { response?: { data?: unknown }; message?: string }
    const data = err.response?.data as { detail?: unknown } | undefined
    if (typeof data?.detail === 'string' && data.detail.trim()) {
      return data.detail
    }
    if (typeof err.message === 'string' && err.message) {
      return err.message
    }
  }
  return fallback
}
