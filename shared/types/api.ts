/** 共享类型定义：API 通用类型。 */

/** 分页参数 */
export interface PaginationParams {
  page?: number
  size?: number
}

/** 分页响应 */
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  size: number
}

/** API 错误响应 */
export interface ApiError {
  detail: string
}
