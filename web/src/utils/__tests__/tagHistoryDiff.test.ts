/** 操作历史 diff 纯函数单测。 */

import { describe, expect, it } from 'vitest'
import { buildHistoryDiff, formatHistoryValue } from '../tagHistoryDiff'
import type { HistoryItem } from '@/types/tagAdvanced'

function makeItem(overrides: Partial<HistoryItem>): HistoryItem {
  return {
    id: 1,
    batch_id: null,
    operation: 'rename',
    tag_ids: [1],
    tag_names: [],
    before: {},
    after: {},
    meta: null,
    created_at: '2026-08-24T10:00:00Z',
    ...overrides,
  }
}

describe('buildHistoryDiff', () => {
  it('改名操作：只列出名称变化', () => {
    const item = makeItem({
      before: {
        '1': { id: 1, name: '白色', category: 'color', aliases: [], link_count: 0 },
      },
      after: {
        '1': { id: 1, name: '纯白', category: 'color', aliases: [], link_count: 0 },
      },
    })
    const rows = buildHistoryDiff(item)
    expect(rows).toHaveLength(1)
    expect(rows[0].changes.map((c) => c.field)).toEqual(['name'])
    expect(rows[0].changes[0].before).toBe('白色')
    expect(rows[0].changes[0].after).toBe('纯白')
  })

  it('删除操作：标记 deleted 且列出全部字段变化', () => {
    const item = makeItem({
      operation: 'delete',
      before: { '5': { id: 5, name: '孤儿甲', category: 'free', aliases: [], link_count: 0 } },
      after: { '5': { deleted: true, name: '孤儿甲' } },
    })
    const rows = buildHistoryDiff(item)
    expect(rows[0].deleted).toBe(true)
    expect(rows[0].changes.length).toBeGreaterThan(0)
  })

  it('无变化字段不列出', () => {
    const item = makeItem({
      before: { '1': { id: 1, name: '甲', category: 'free', aliases: [], link_count: 0 } },
      after: { '1': { id: 1, name: '甲', category: 'free', aliases: [], link_count: 0 } },
    })
    expect(buildHistoryDiff(item)[0].changes).toEqual([])
  })

  it('别名变化被识别', () => {
    const item = makeItem({
      before: { '1': { id: 1, name: '甲', category: 'free', aliases: [], link_count: 0 } },
      after: { '1': { id: 1, name: '甲', category: 'free', aliases: ['乙'], link_count: 0 } },
    })
    const changes = buildHistoryDiff(item)[0].changes
    expect(changes.map((c) => c.field)).toContain('aliases')
  })
})

describe('formatHistoryValue', () => {
  it('null/undefined 显示占位符', () => {
    expect(formatHistoryValue(null)).toBe('—')
    expect(formatHistoryValue(undefined)).toBe('—')
  })

  it('数组拼接、对象转字符串', () => {
    expect(formatHistoryValue(['白色', '米白'])).toBe('白色、米白')
    expect(formatHistoryValue(0)).toBe('0')
  })
})
