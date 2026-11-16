/** 标签相关的 UI 常量（类别 / 来源中文映射等）。
 *
 * 从 api/tags.ts 迁来，保持 API 层只负责请求；多处复用集中于此。
 */

import { TAG_CATEGORY_LABELS } from '@shared/types/inspiration'

/** 类别名称的中文映射（类别值为 snake_case，同时是类别下拉选项的来源）。
 *
 * 真源在 `shared/types/inspiration.ts` 的 TAG_CATEGORY_LABELS（Web / Mobile 共用）；
 * 这里展开成 Record<string, string>，让 `CATEGORY_LABELS[record.category]`
 * （category 为后端返回的任意字符串）继续可用。新增类别只改 shared 一处。
 */
export const CATEGORY_LABELS: Record<string, string> = { ...TAG_CATEGORY_LABELS }

/** 来源的中文映射 */
export const SOURCE_LABELS: Record<string, string> = {
  seed: '预设',
  ai_generated: 'AI生成',
  manual: '手动',
}
