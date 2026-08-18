/** nearDup 决策纯函数单测：覆盖保留左边/右边/跳过与多张组边界。 */

import { describe, expect, it } from 'vitest'
import { collectIdsToDelete, type DupDecision } from '@/utils/nearDup'
import type { NearDuplicateGroup } from '@/api/admin'

/** 构造测试用近似重复组（files 按评分降序，第一张为建议保留） */
function makeGroup(ids: string[], keeperId = ids[0]): NearDuplicateGroup {
  return {
    rep_phash: 'x'.repeat(48),
    keeper_id: keeperId,
    wasted_bytes: 0,
    files: ids.map((id) => ({
      id,
      file_path: `images/${id}.jpg`,
      thumbnail_path: null,
      is_favorite: false,
      created_at: '2026-08-01T00:00:00',
      size_bytes: 100,
      score: 0,
      distance: 0,
    })),
  }
}

describe('collectIdsToDelete', () => {
  it('keep-left：两张组删除右边一张', () => {
    const g = makeGroup(['a', 'b'])
    expect(collectIdsToDelete(g, 'keep-left')).toEqual(['b'])
  })

  it('keep-left：三张组删除除第一张外全部', () => {
    const g = makeGroup(['a', 'b', 'c'])
    expect(collectIdsToDelete(g, 'keep-left')).toEqual(['b', 'c'])
  })

  it('keep-right：两张组删除左边一张', () => {
    const g = makeGroup(['a', 'b'])
    expect(collectIdsToDelete(g, 'keep-right')).toEqual(['a'])
  })

  it('keep-right：三张组保留第二张，删除其余', () => {
    const g = makeGroup(['a', 'b', 'c'])
    expect(collectIdsToDelete(g, 'keep-right')).toEqual(['a', 'c'])
  })

  it('skip：都保留，不删除任何素材', () => {
    const g = makeGroup(['a', 'b', 'c'])
    expect(collectIdsToDelete(g, 'skip')).toEqual([])
  })

  it('多张组 keep-right 保留的是第二张而非 keeper', () => {
    // keeper 为 a，但用户选择保留右边（b）
    const g = makeGroup(['a', 'b', 'c'], 'a')
    expect(collectIdsToDelete(g, 'keep-right')).toEqual(['a', 'c'])
  })

  it('空 files 防御：任何决策都返回空', () => {
    const g = makeGroup([], '')
    for (const d of ['keep-left', 'keep-right', 'skip'] as DupDecision[]) {
      expect(collectIdsToDelete(g, d)).toEqual([])
    }
  })
})
