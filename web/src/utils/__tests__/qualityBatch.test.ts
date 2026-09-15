/**
 * 批量质量审核的常量与耗时估算测试。
 *
 * 关注点：上限必须与后端 `le=5000` 一致（否则前端放行、后端 422），
 * 以及耗时文案要按实测 12.5 秒/张给用户一个可判断的量级。
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

  it('提示文案包含数量、耗时与可中断说明', () => {
    const text = qualityBatchHint(5000)
    expect(text).toContain('5000 条')
    expect(text).toContain('17.4 小时')
    expect(text).toContain('暂停/取消')
    expect(qualityBatchHint(0)).toBe('0 条')
  })
})
