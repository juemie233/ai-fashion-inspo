/** 标签类别契约（Mobile 侧）：shared 真源与移动端展示映射一致。 */

import { TAG_CATEGORIES, TAG_CATEGORY_LABELS } from '@shared/types/inspiration'

import { CATEGORY_LABELS, tagCategoryLabel } from '../constants/labels'

describe('标签分类中文名', () => {
  it('shared 每个类别都有中文名，且不是未翻译的原值', () => {
    for (const category of TAG_CATEGORIES) {
      const label = TAG_CATEGORY_LABELS[category]
      expect(label).toBeTruthy()
      expect(label).not.toBe(category)
    }
  })

  it('移动端映射与 shared 类别集合完全一致（无遗漏、无多余）', () => {
    expect(Object.keys(CATEGORY_LABELS).sort()).toEqual([...TAG_CATEGORIES].sort())
  })

  it('原先缺映射的三类（material / leg_posture / outfit）现在显示中文', () => {
    expect(tagCategoryLabel('material')).toBe('面料')
    expect(tagCategoryLabel('leg_posture')).toBe('腿部姿态')
    expect(tagCategoryLabel('outfit')).toBe('穿搭大标签')
  })

  it('未知类别回退原值，空值回退「其他」', () => {
    expect(tagCategoryLabel('unknown_category')).toBe('unknown_category')
    expect(tagCategoryLabel(null)).toBe('其他')
    expect(tagCategoryLabel('')).toBe('其他')
  })
})
