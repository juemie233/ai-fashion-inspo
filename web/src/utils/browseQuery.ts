/** 素材库列表请求参数的统一构建：从 URL query / 筛选状态映射到后端参数。
 *
 * HomeView 与 DetailView（上一张/下一张浏览上下文）共用本函数，
 * 保证「详情页看到的相邻素材」与「列表页进入时的筛选排序」完全一致。
 */

/** 素材库默认排序 */
export const DEFAULT_BROWSE_SORT = 'newest'

/** 排序偏好持久化 key（与 HomeView 共用） */
export const SORT_STORAGE_KEY = 'masonry-sort'

/** 每页数量偏好持久化 key（与 HomeView 共用） */
export const PAGE_SIZE_STORAGE_KEY = 'masonry-page-size'

/** 读取本地持久化的排序偏好，缺省回退 newest */
export function storedBrowseSort(): string {
  return localStorage.getItem(SORT_STORAGE_KEY) || DEFAULT_BROWSE_SORT
}

/** 读取本地持久化的每页数量，缺省 50 */
export function storedBrowsePageSize(): number {
  const n = parseInt(localStorage.getItem(PAGE_SIZE_STORAGE_KEY) || '', 10)
  // 有效值夹在 [1, 200]：避免本地存储被篡改后请求超大分页
  return Number.isFinite(n) && n > 0 ? Math.min(Math.max(n, 1), 200) : 50
}

/**
 * 将筛选状态映射为素材库列表请求参数。
 *
 * 参数:
 *     state: 筛选状态（URL query 或页面筛选 ref 的当前值）
 *     page: 页码（从 1 开始）
 *     size: 每页数量
 */
export function buildBrowseParams(
  state: Record<string, any>,
  page: number,
  size: number,
) {
  return {
    page,
    size,
    source_type: state.source && state.source !== 'all' ? state.source : undefined,
    media_type: state.media && state.media !== 'all' ? state.media : undefined,
    is_favorite: state.status === 'favorites' ? true : undefined,
    analysis_status:
      state.status === 'done' || state.status === 'pending' ? state.status : undefined,
    tag_status: state.status === 'untagged' ? 'untagged' : undefined,
    quality_status:
      state.quality && state.quality !== 'all' && state.quality !== 'ai'
        ? state.quality
        : undefined,
    is_ai_generated: state.quality === 'ai' ? true : undefined,
    include_tags: state.tags ? state.tags : undefined,
    dominant_color: state.color ? state.color : undefined,
    date_from: state.date_from || undefined,
    date_to: state.date_to || undefined,
    sort: state.sort || storedBrowseSort(),
  }
}
