/** 操作历史详情展示的纯函数：before/after 差异计算与值格式化。 */

import type { HistoryItem } from '@/types/tagAdvanced'

/** 快照字段的中文名（diff 展示用） */
export const HISTORY_FIELD_LABELS: Record<string, string> = {
  name: '名称',
  category: '类别',
  parent_id: '父标签',
  pinned: '置顶',
  sort_order: '排序',
  description: '备注',
  source: '来源',
  aliases: '别名',
  link_count: '关联数',
}

export interface HistoryDiffRow {
  tag_id: number
  name: string
  deleted: boolean
  changes: Array<{ field: string; label: string; before: unknown; after: unknown }>
}

/** 计算单个历史项的 before/after 差异（每标签一行，仅列出发生变化的字段） */
export function buildHistoryDiff(item: HistoryItem): HistoryDiffRow[] {
  const tagIds = Object.keys(item.after).length ? Object.keys(item.after) : Object.keys(item.before)
  return tagIds.map((tid) => {
    const before = item.before[tid] as Record<string, unknown> | undefined
    const after = item.after[tid] as Record<string, unknown> | undefined
    const deleted = Boolean((after as { deleted?: boolean } | undefined)?.deleted)
    const changes: HistoryDiffRow['changes'] = []
    for (const [field, label] of Object.entries(HISTORY_FIELD_LABELS)) {
      const b = before?.[field]
      const a = after?.[field]
      const norm = (v: unknown) => JSON.stringify(v ?? null)
      if (norm(b) !== norm(a)) {
        changes.push({ field, label, before: b ?? null, after: a ?? null })
      }
    }
    return {
      tag_id: Number(tid),
      name: (before?.name as string) ?? (after?.name as string) ?? `#${tid}`,
      deleted,
      changes,
    }
  })
}

/** 详情弹窗值格式化（null/数组/对象 → 可读文本） */
export function formatHistoryValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return (v as unknown[]).join('、') || '—'
  return String(v)
}
