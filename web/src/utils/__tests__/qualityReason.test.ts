import { describe, expect, it } from 'vitest'
import { QUALITY_REASON_OPTIONS, qualityReasonLabel } from '../qualityReason'

describe('qualityReason', () => {
  it('把已知原因码转成中文', () => {
    expect(qualityReasonLabel('not_outfit')).toBe('不是穿搭')
    expect(qualityReasonLabel('prefilter_reject')).toBe('初筛器判定垃圾')
  })

  it('空值与未知码的处理', () => {
    expect(qualityReasonLabel(null)).toBe('')
    expect(qualityReasonLabel(undefined)).toBe('')
    // 未知码原样返回，避免后端新增枚举时前端静默显示空白
    expect(qualityReasonLabel('brand_new_code')).toBe('brand_new_code')
  })

  it('筛选项首项是「全部原因」，值为空串', () => {
    expect(QUALITY_REASON_OPTIONS[0]).toEqual({ label: '全部原因', value: '' })
    expect(QUALITY_REASON_OPTIONS.length).toBeGreaterThan(1)
    expect(QUALITY_REASON_OPTIONS.every((o) => o.label && typeof o.value === 'string')).toBe(true)
  })
})
