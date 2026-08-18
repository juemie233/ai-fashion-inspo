/** 手机图剪裁结果归一化工具单测。 */

import { describe, expect, it } from 'vitest'
import { normalizeApplyResult } from '../cropResult'

describe('normalizeApplyResult', () => {
  it('完整结构原样保留', () => {
    const data = {
      processed: 2,
      skipped: [{ id: 'a', reason: '文件不存在' }],
      duplicates: [{ id: 'b', dup_id: 'c', reason: '内容重复' }],
      backup_dir: '/tmp/backup',
      vector_task_id: 7,
    }
    expect(normalizeApplyResult(data)).toEqual(data)
  })

  it('duplicates/skipped 缺失或为 null 时归一化为空数组（弹窗判断不再崩溃）', () => {
    expect(normalizeApplyResult({}).duplicates).toEqual([])
    expect(normalizeApplyResult({}).skipped).toEqual([])
    expect(normalizeApplyResult({ duplicates: null, skipped: undefined }).duplicates).toEqual([])
    expect(normalizeApplyResult(null).duplicates).toEqual([])
    expect(normalizeApplyResult(undefined).skipped).toEqual([])
  })

  it('缺失数值/字符串字段给默认值', () => {
    const r = normalizeApplyResult({ processed: undefined })
    expect(r.processed).toBe(0)
    expect(r.backup_dir).toBeNull()
    expect(r.vector_task_id).toBeNull()
  })

  it('duplicates 非空数组时原样透传（弹窗展示依赖该数组）', () => {
    const dups = [{ id: 'x', dup_id: 'y', reason: '裁剪结果与素材 y 内容重复' }]
    const r = normalizeApplyResult({ duplicates: dups })
    expect(r.duplicates).toEqual(dups)
    expect(r.duplicates.length).toBe(1)
  })
})
