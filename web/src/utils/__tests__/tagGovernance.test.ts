/** 标签治理纯函数测试：用量分桶 / 重命名预览（含后端预检口径）/ 相似对分类。 */

import { describe, expect, it } from 'vitest'

import {
  bucketOf,
  buildRenamePreview,
  classifyPair,
  MERGE_SIMILARITY,
  summarizeCategoryChange,
  summarizeUsage,
  USAGE_BUCKETS,
} from '../tagGovernance'
// 类型也一并钉住：它们出现在导出函数的签名里，改动会在这里先炸
import type { PairAction, RenamePreview, RenamePreviewRow, UsageBucket } from '../tagGovernance'

describe('用量分桶', () => {
  it('按区间归桶，边界值落在低桶', () => {
    expect(bucketOf(0)).toBe('zero')
    expect(bucketOf(1)).toBe('one')
    expect(bucketOf(2)).toBe('two')
    expect(bucketOf(3)).toBe('few')
    expect(bucketOf(5)).toBe('few')
    expect(bucketOf(6)).toBe('mid')
    expect(bucketOf(10)).toBe('mid')
    expect(bucketOf(11)).toBe('many')
    expect(bucketOf(50)).toBe('many')
    expect(bucketOf(51)).toBe('core')
    expect(bucketOf(1937)).toBe('core')
  })

  it('缺失/非法用量按 0 处理，不产生空桶', () => {
    expect(bucketOf(undefined)).toBe('zero')
    expect(bucketOf(null)).toBe('zero')
    expect(bucketOf(-5)).toBe('zero')
    expect(bucketOf(Number.NaN)).toBe('zero')
  })

  it('统计每桶数量，且覆盖全部桶', () => {
    const rows: Array<UsageBucket & { count: number }> = summarizeUsage([
      { usage_count: 0 },
      { usage_count: 1 },
      { usage_count: 1 },
      { usage_count: 4 },
      { usage_count: 100 },
      {}, // 缺字段 → zero
    ])
    expect(rows.map((r) => r.key)).toEqual(USAGE_BUCKETS.map((b) => b.key))
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r.count]))
    expect(byKey.zero).toBe(2)
    expect(byKey.one).toBe(2)
    expect(byKey.few).toBe(1)
    expect(byKey.core).toBe(1)
    expect(rows.reduce((sum, r) => sum + r.count, 0)).toBe(6)
  })
})

describe('重命名预览', () => {
  const tags = [
    { id: 1, name: '白色衬衫' },
    { id: 2, name: '白色长裙' },
    { id: 3, name: '黑色外套' },
  ]

  it('只列命中的标签，给出前后对照与改名数', () => {
    const preview: RenamePreview = buildRenamePreview(tags, '白色', '纯白')
    const rows: RenamePreviewRow[] = preview.rows
    expect(rows).toHaveLength(2)
    expect(preview.rows.map((r) => r.before)).toEqual(['白色衬衫', '白色长裙'])
    expect(preview.rows[0].after).toBe('纯白衬衫')
    expect(preview.changedCount).toBe(2)
    expect(preview.conflicts).toEqual([])
  })

  it('全局替换（与后端 replace 行为一致）', () => {
    const preview = buildRenamePreview([{ id: 1, name: '白色白色' }], '白色', '纯白')
    expect(preview.rows[0].after).toBe('纯白纯白')
  })

  it('替换为空名 → 标出「后端会 400」', () => {
    const preview = buildRenamePreview([{ id: 1, name: '白色' }], '白色', '')
    expect(preview.rows[0].empty).toBe(true)
    expect(preview.changedCount).toBe(0)
    expect(preview.conflicts[0]).toContain('空名')
  })

  it('批内替换后同名 → 标出「后端会 409」', () => {
    // 「白纯」与「纯白」都把「白」换成「纯」→ 都变成「纯纯」（后端预检会 409）
    const preview = buildRenamePreview(
      [
        { id: 1, name: '白纯' },
        { id: 2, name: '纯白' },
      ],
      '白',
      '纯',
    )
    expect(preview.rows.map((r) => r.after)).toEqual(['纯纯', '纯纯'])
    expect(preview.conflicts.some((c) => c.includes('409'))).toBe(true)
  })

  it('空查找词：不产生预览行（避免整表被替换）', () => {
    expect(buildRenamePreview(tags, '', 'X')).toEqual({ rows: [], changedCount: 0, conflicts: [] })
  })

  it('没命中任何标签：空预览', () => {
    expect(buildRenamePreview(tags, '紫色', 'X').rows).toEqual([])
  })
})

describe('批量改类别影响面', () => {
  it('分开统计「会变」与「本来就是目标类别」', () => {
    const result = summarizeCategoryChange(
      [{ category: 'style' }, { category: 'color' }, { category: 'style' }],
      'style',
    )
    expect(result).toEqual({ changed: 1, unchanged: 2 })
  })
})

describe('相似对处理建议', () => {
  it('高相似建议合并，中相似建议设别名', () => {
    const action: PairAction = classifyPair(0.95)
    expect(action).toBe('merge')
    expect(classifyPair(MERGE_SIMILARITY)).toBe('merge')
    expect(classifyPair(0.74)).toBe('alias')
    expect(classifyPair(0.6)).toBe('alias')
  })
})
