/**
 * cropSelection 纯函数单测：手机图剪裁扫描候选的默认勾选口径。
 *
 * 重点覆盖「抖音截图内容边界检测（content）扫描后默认全选」——
 * 包括 auto_ok=false 的残留候选与低置信候选也要勾选（列表整体可信），
 * 以及 auto 模式仍沿用后端 auto_checked 决策不受影响。
 */

import { describe, expect, it } from 'vitest'

import { defaultCheckedIds, type CropSelectionCandidate } from '@/utils/cropSelection'

/** 构造候选（仅列出判定相关字段，其余用默认值补齐） */
function candidate(over: Partial<CropSelectionCandidate> & { id: string }): CropSelectionCandidate {
  return {
    auto_ok: true,
    auto_checked: null,
    confidence: 'high',
    boundary_kind: 'gray_band',
    crop_top: 0,
    ...over,
  }
}

describe('defaultCheckedIds —— content（抖音截图内容边界检测）默认全选', () => {
  it('全部候选默认勾选（含 auto_ok=false 与低置信候选）', () => {
    const items = [
      candidate({ id: 'a', auto_ok: true, auto_checked: true }),
      candidate({ id: 'b', auto_ok: true, auto_checked: false }),
      candidate({ id: 'c', auto_ok: false, auto_checked: false, crop_top: 0 }),
      candidate({ id: 'd', confidence: 'low', boundary_kind: 'plain' }),
    ]
    const checked = defaultCheckedIds(items, 'content')
    expect([...checked].sort()).toEqual(['a', 'b', 'c', 'd'])
  })

  it('空候选列表返回空集合', () => {
    expect(defaultCheckedIds([], 'content').size).toBe(0)
  })

  it('重新扫描传入的全新列表同样全部勾选（不残留上次手动取消）', () => {
    const first = defaultCheckedIds([candidate({ id: 'a' }), candidate({ id: 'b' })], 'content')
    // 用户手动取消 b 后再重新扫描：新列表整体重置为全选
    first.delete('b')
    const rescanned = defaultCheckedIds(
      [candidate({ id: 'a' }), candidate({ id: 'b' }), candidate({ id: 'c' })],
      'content',
    )
    expect(rescanned.size).toBe(3)
  })
})

describe('defaultCheckedIds —— auto（小红书截图黑边检测）沿用后端决策', () => {
  it('auto_checked=true 勾选、false 不勾选', () => {
    const items = [
      candidate({ id: 'x', auto_checked: true }),
      candidate({ id: 'y', auto_checked: false }),
    ]
    expect([...defaultCheckedIds(items, 'auto')]).toEqual(['x'])
  })

  it('旧响应无 auto_checked 时按历史规则回退（低置信/plain 不勾选）', () => {
    const items = [
      candidate({ id: 'ok', auto_checked: undefined }),
      candidate({ id: 'low', auto_checked: undefined, confidence: 'low' }),
      candidate({ id: 'plain', auto_checked: undefined, boundary_kind: 'plain' }),
    ]
    expect([...defaultCheckedIds(items, 'auto')]).toEqual(['ok'])
  })

  it('auto_ok=false 的残留候选：有 auto_checked 决策时以决策为准，旧响应按建议比例兜底', () => {
    const items = [
      candidate({ id: 'residue-on', auto_ok: false, auto_checked: true }),
      candidate({ id: 'residue-off', auto_ok: false, auto_checked: false }),
      candidate({ id: 'legacy', auto_ok: false, auto_checked: undefined, crop_top: 8 }),
      candidate({ id: 'legacy-none', auto_ok: false, auto_checked: undefined, crop_top: 0 }),
    ]
    expect([...defaultCheckedIds(items, 'auto')].sort()).toEqual(['legacy', 'residue-on'])
  })
})
