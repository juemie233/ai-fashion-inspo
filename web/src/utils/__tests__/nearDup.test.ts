/** nearDup 决策纯函数单测：覆盖保留左边/右边/跳过与多张组边界。 */

import { describe, expect, it } from 'vitest'
import {
  collectIdsToDelete,
  dropSubmittedFiles,
  nearDupScopeLabel,
  type DupDecision,
} from '@/utils/nearDup'
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

  it('delete-both：两张组删除当前对比的两张', () => {
    const g = makeGroup(['a', 'b'])
    expect(collectIdsToDelete(g, 'delete-both')).toEqual(['a', 'b'])
  })

  it('delete-both：多张组仅删除前两张，组内其余不处理', () => {
    const g = makeGroup(['a', 'b', 'c', 'd'])
    expect(collectIdsToDelete(g, 'delete-both')).toEqual(['a', 'b'])
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

describe('dropSubmittedFiles', () => {
  it('两张组删掉一张：不足 2 张，整组从列表移出（不再被弹窗重复对待）', () => {
    const g = makeGroup(['a', 'b'])
    expect(dropSubmittedFiles([g], new Set(['b']))).toEqual([])
  })

  it('四张组删两张：仍有 2 张，保留该组且只剩未删的素材', () => {
    const g = makeGroup(['a', 'b', 'c', 'd'])
    const out = dropSubmittedFiles([g], new Set(['c', 'd']))
    expect(out).toHaveLength(1)
    expect(out[0].files.map((f) => f.id)).toEqual(['a', 'b'])
  })

  it('「都保留（跳过）」的组不受影响：决定删除的 ID 为空时原样返回', () => {
    const groups = [makeGroup(['a', 'b']), makeGroup(['c', 'd'])]
    expect(dropSubmittedFiles(groups, new Set())).toBe(groups)
  })

  it('只影响命中的组：未命中的组原样保留', () => {
    const hit = makeGroup(['a', 'b'])
    const other = makeGroup(['c', 'd'])
    const out = dropSubmittedFiles([hit, other], new Set(['b']))
    expect(out).toHaveLength(1)
    expect(out[0].files.map((f) => f.id)).toEqual(['c', 'd'])
  })

  it('不改动入参：返回新组对象，原组 files 不被就地修改', () => {
    const g = makeGroup(['a', 'b', 'c'])
    dropSubmittedFiles([g], new Set(['c']))
    expect(g.files.map((f) => f.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('nearDupScopeLabel', () => {
  it('缓存完整 + limit=0：报「全库扫描 N 张」', () => {
    expect(
      nearDupScopeLabel({ scanned: 19568, total: 19568, truncated: false, missing: 0 }, 0),
    ).toBe('全库扫描 19568 张')
  })

  it('随机抽样（limit>0）：报「随机扫描 N / M 张」', () => {
    expect(
      nearDupScopeLabel({ scanned: 500, total: 19568, truncated: true, missing: 0 }, 500),
    ).toBe('随机扫描 500 / 19568 张')
  })

  it('缓存未补齐：不说「全库/随机」，如实说明只覆盖已缓存部分', () => {
    // 用户看到的超时事故场景：11k 张没缓存，扫描只覆盖一半
    expect(
      nearDupScopeLabel({ scanned: 11762, total: 23904, truncated: true, missing: 12142 }, 0),
    ).toBe('已扫描已缓存的 11762 张（哈希缓存还差 12142 张未补齐）')
  })

  it('缓存未补齐时优先于随机抽样口径（避免把缓存问题说成抽样）', () => {
    expect(
      nearDupScopeLabel({ scanned: 300, total: 2000, truncated: true, missing: 20 }, 500),
    ).toBe('已扫描已缓存的 300 张（哈希缓存还差 20 张未补齐）')
  })
})
