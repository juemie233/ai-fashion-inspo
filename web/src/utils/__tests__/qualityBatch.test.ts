/**
 * 批量质量审核的常量与耗时估算测试。
 *
 * 关注点：上限必须与后端 `le=5000` 一致（否则前端放行、后端 422），
 * 以及耗时文案要按**实测平均耗时**（后端 `/ai/quality-stats` 的 avg_seconds）给量级，
 * 没有实测样本时才退回兜底常量并明确标注来源。
 */

import { describe, expect, it } from 'vitest'
import {
  QUALITY_BATCH_DEFAULT,
  QUALITY_BATCH_MAX,
  QUALITY_SEC_PER_ITEM,
  estimateQualityHours,
  qualityBatchHint,
} from '../qualityBatch'

describe('qualityBatch', () => {
  it('上限与后端校验一致（5000）', () => {
    expect(QUALITY_BATCH_MAX).toBe(5000)
    expect(QUALITY_BATCH_DEFAULT).toBeLessThanOrEqual(QUALITY_BATCH_MAX)
    expect(QUALITY_SEC_PER_ITEM).toBe(12.5)
  })

  it('按 12.5 秒/张估算耗时', () => {
    expect(estimateQualityHours(0)).toBe(0)
    expect(estimateQualityHours(-5)).toBe(0)
    expect(estimateQualityHours(288)).toBe(1) // 288 × 12.5s = 3600s
    expect(estimateQualityHours(200)).toBe(0.7) // 2500s ≈ 0.69h
    expect(estimateQualityHours(5000)).toBe(17.4)
  })

  it('并发大于 1 时按并发折算', () => {
    expect(estimateQualityHours(2000, 2)).toBe(3.5)
    expect(estimateQualityHours(2000, 0)).toBe(6.9) // 非法并发回退为 1
  })

  it('估算支持传入实测平均耗时（覆盖兜底常量）', () => {
    // 1000 条 × 5 秒 = 5000 秒 ≈ 1.4 小时
    expect(estimateQualityHours(1000, 1, 5)).toBe(1.4)
    const hint = qualityBatchHint(1000, 1, 5)
    expect(hint).toContain('5 秒/张')
    expect(hint).toContain('实测均值')
  })

  it('未传实测值时用兜底常量并标注来源', () => {
    const hint = qualityBatchHint(100)
    expect(hint).toContain('12.5 秒/张')
    expect(hint).toContain('兜底估值')
  })

  it('提示文案包含数量、耗时与可中断说明', () => {
    const text = qualityBatchHint(5000)
    expect(text).toContain('5000 条')
    expect(text).toContain('17.4 小时')
    expect(text).toContain('暂停/取消')
    expect(qualityBatchHint(0)).toBe('0 条')
  })
})
