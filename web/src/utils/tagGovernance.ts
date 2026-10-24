/** 标签治理的纯函数：用量分桶、重命名预览、重复对分类。
 *
 * 为什么单独抽出来：这三条都是「**执行前必须给人看到**」的口径——
 * - 分桶决定长尾治理动谁；
 * - 重命名预览要能提前暴露「改完为空名」「批内撞名」这两类会被后端 400/409 挡掉的情况；
 * - 相似度分类决定这一对是「合并」（改素材关联）还是「设别名」（只做归一化，不动关联）。
 * 组件只负责渲染，规则在这里单测。
 */

/** 用量分桶（与标签库实际分布对齐：70% 的标签只用过 1~2 次） */
export interface UsageBucket {
  key: string
  label: string
  min: number
  max: number
}

export const USAGE_BUCKETS: UsageBucket[] = [
  { key: 'zero', label: '0 次（未使用）', min: 0, max: 0 },
  { key: 'one', label: '1 次', min: 1, max: 1 },
  { key: 'two', label: '2 次', min: 2, max: 2 },
  { key: 'few', label: '3~5 次', min: 3, max: 5 },
  { key: 'mid', label: '6~10 次', min: 6, max: 10 },
  { key: 'many', label: '11~50 次', min: 11, max: 50 },
  { key: 'core', label: '51 次以上', min: 51, max: Number.MAX_SAFE_INTEGER },
]

/** 按用量归桶（未知/负数按 0 处理，避免出现空桶） */
export function bucketOf(usage: number | null | undefined): string {
  const value = typeof usage === 'number' && Number.isFinite(usage) ? Math.max(0, usage) : 0
  const bucket = USAGE_BUCKETS.find((b) => value >= b.min && value <= b.max)
  return bucket ? bucket.key : 'zero'
}

/** 统计每个桶里有多少标签（给分桶筛选器显示数量） */
export function summarizeUsage(
  tags: Array<{ usage_count?: number | null }>,
): Array<UsageBucket & { count: number }> {
  const counts = new Map<string, number>(USAGE_BUCKETS.map((b) => [b.key, 0]))
  for (const tag of tags) {
    const key = bucketOf(tag.usage_count)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return USAGE_BUCKETS.map((b) => ({ ...b, count: counts.get(b.key) ?? 0 }))
}

/** 重命名预览的一行（before/after 对照） */
export interface RenamePreviewRow {
  id: number
  before: string
  after: string
  /** 名字确实会变 / 变了但为空名（此类会被后端拒绝，前端标红） */
  changed: boolean
  empty: boolean
}

/** 重命名预览结果 */
export interface RenamePreview {
  rows: RenamePreviewRow[]
  /** 实际会改名的数量 */
  changedCount: number
  /** 会被后端挡下的原因（空名 / 批内撞名），执行前就告诉用户 */
  conflicts: string[]
}

/**
 * 计算「查找替换」的预览。
 *
 * 与后端 `batch_rename_tags` 的预检规则保持一致：
 * - 替换后为空名 → 后端 400；
 * - 批内两个标签替换后同名 → 后端 409；
 * （与库里其它标签同名/与别名同名的冲突需要查库，预览只标注前两类，执行时仍由后端兜底。）
 */
export function buildRenamePreview(
  tags: Array<{ id: number; name: string }>,
  find: string,
  replace: string,
): RenamePreview {
  const keyword = find ?? ''
  const rows: RenamePreviewRow[] = []
  if (!keyword) {
    return { rows, changedCount: 0, conflicts: [] }
  }
  for (const tag of tags) {
    if (!tag.name.includes(keyword)) continue
    // 全局替换：与后端 str.replace 的默认行为一致（后端是 replace(str, str) → 全替换）
    const after = tag.name.split(keyword).join(replace ?? '')
    const changed = after !== tag.name
    rows.push({ id: tag.id, before: tag.name, after, changed, empty: after.trim() === '' })
  }

  const conflicts: string[] = []
  const emptyRows = rows.filter((r) => r.empty)
  if (emptyRows.length > 0) {
    conflicts.push(`有 ${emptyRows.length} 个标签替换后会变成空名，后端会拒绝（400）`)
  }
  const seen = new Map<string, string>()
  for (const row of rows) {
    if (row.empty) continue
    const prev = seen.get(row.after)
    if (prev !== undefined) {
      conflicts.push(`「${prev}」与「${row.before}」替换后同名「${row.after}」，后端会拒绝（409）`)
      break
    }
    seen.set(row.after, row.before)
  }
  return { rows, changedCount: rows.filter((r) => r.changed && !r.empty).length, conflicts }
}

/** 批量改类别的影响面：多少个会变、多少个本来就是目标类别 */
export function summarizeCategoryChange(
  tags: Array<{ category: string }>,
  target: string,
): { changed: number; unchanged: number } {
  let changed = 0
  let unchanged = 0
  for (const tag of tags) {
    if (tag.category === target) {
      unchanged += 1
    } else {
      changed += 1
    }
  }
  return { changed, unchanged }
}

/** 相似标签对的处理建议：高相似=重复（合并），中相似=同义变体（设别名） */
export type PairAction = 'merge' | 'alias'

/** 合并/别名的分界线：≥ 该值视为重复，低于它视为同义变体 */
export const MERGE_SIMILARITY = 0.75

/**
 * 按相似度给建议动作。
 *
 * - ≥ 0.75：几乎就是同一个词，合并（把关联并过去）最干净；
 * - 0.6~0.75：同义/近义变体，**设别名**更安全——别名只影响以后 AI 打标的归一化，
 *   不动已有素材关联，改错了也容易撤。
 */
export function classifyPair(similarity: number): PairAction {
  return similarity >= MERGE_SIMILARITY ? 'merge' : 'alias'
}
