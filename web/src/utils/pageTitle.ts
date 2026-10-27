/** 浏览器标签页标题口径：统一为「<页面>[-<子页面 | 实体名>] - AI 穿搭素材库」。
 *
 * 为什么单独成 util：同一套口径要在三处复用——路由守卫（URL 驱动的子页面）、
 * 视图（数据加载后才知道的实体名，如人物网名）、单测。散落在各处必然漂移。
 *
 * 分工：
 * - **URL 驱动的子页面**（`?tab=` / `?kind=`）由 `resolveDocumentTitle` 解析，切页签即变；
 * - **运行时才知道的实体名**（人物网名、合集名、素材作者）由 `composables/usePageTitle`
 *   覆盖——不能写在这里，静态表拿不到数据。
 */

export const APP_NAME = 'AI 穿搭素材库'

/** 一个页面的子页面规则 */
interface SubpageSpec {
  /** 子页面在 URL 上的 query 参数名 */
  queryKey: string
  /** 未带该参数时（默认子页面）显示的 key；缺省则只显示页面名 */
  defaultKey?: string
  /** 子页面 key → 中文标签 */
  labels: Record<string, string>
}

/** 子页面登记表：key 与 router 的 `name` 对齐。
 *
 * 只登记「子页面状态写在 URL 里」的页面——状态存在 localStorage / 组件内 ref 的
 * （如采集管理的页签）由视图自己调 `usePageTitle`，否则路由守卫读不到当前页签。
 */
export const SUBPAGE_SPECS: Record<string, SubpageSpec> = {
  admin: {
    queryKey: 'tab',
    defaultKey: 'overview',
    labels: { overview: '概览', ai: '疑似 AI 素材', crop: '手机图剪裁', trash: '垃圾桶' },
  },
  'admin-governance': {
    queryKey: 'tab',
    defaultKey: 'cleanup',
    labels: {
      cleanup: '批量清理',
      integrity: '数据完整性',
      duplicates: '重复文件',
      neardup: '近似重复',
    },
  },
  'admin-insights': {
    queryKey: 'tab',
    defaultKey: 'reports',
    labels: { vectors: '向量管理', reports: '数据报表', prompt: '提示词质量' },
  },
  models: {
    queryKey: 'tab',
    defaultKey: 'models',
    labels: {
      models: '模型管理',
      review: '质量审核',
      queue: '标签分析',
      settings: '参数调优',
      quality: '分析质量',
    },
  },
  'face-scan': {
    queryKey: 'tab',
    defaultKey: 'pending',
    labels: {
      pending: '待审核候选',
      confirmed: '已确认',
      unmatched: '未匹配人脸',
      cluster: '聚合分组',
    },
  },
  persons: {
    queryKey: 'kind',
    defaultKey: 'blogger',
    labels: { blogger: '穿搭博主', model: '职业模特' },
  },
  'tags-advanced': {
    queryKey: 'tab',
    defaultKey: 'health',
    labels: {
      health: '健康度',
      cluster: '聚类',
      network: '网络图',
      effect: '效果分析',
      tree: '层级树',
      history: '历史记录',
      governance: '标签治理',
    },
  },
}

/** 拼成最终标题；空段落回退到站点名（避免出现「 - AI 穿搭素材库」）。 */
export function formatDocumentTitle(segment?: string | null): string {
  const text = (segment ?? '').trim()
  if (!text || text === APP_NAME) return APP_NAME
  return `${text} - ${APP_NAME}`
}

/** 取 query 里的单值（vue-router 对重复参数会给数组） */
function queryValue(query: Record<string, unknown> | undefined, key: string): string | undefined {
  const raw = query?.[key]
  const first = Array.isArray(raw) ? raw[0] : raw
  return typeof first === 'string' && first ? first : undefined
}

/** 由路由解析出「页面-子页面」段落（不含站点名） */
export function resolveRouteSegment(route: {
  name?: unknown
  meta?: { title?: unknown } | null
  query?: Record<string, unknown>
}): string {
  const metaTitle = route.meta?.title
  const base = typeof metaTitle === 'string' && metaTitle.trim() ? metaTitle.trim() : APP_NAME
  const name = typeof route.name === 'string' ? route.name : ''
  const spec = SUBPAGE_SPECS[name]
  if (!spec) return base
  const key = queryValue(route.query, spec.queryKey) ?? spec.defaultKey
  const label = key ? spec.labels[key] : undefined
  return label ? `${base}-${label}` : base
}

/** 路由 → 完整 document.title */
export function resolveDocumentTitle(route: {
  name?: unknown
  meta?: { title?: unknown } | null
  query?: Record<string, unknown>
}): string {
  return formatDocumentTitle(resolveRouteSegment(route))
}
