/** 标签类别契约：shared 真源与 Web 展示映射必须一致（2026-11-16 跨端契约收口）。 */

import { describe, expect, it } from 'vitest'

import { TAG_CATEGORIES, TAG_CATEGORY_LABELS } from '@shared/types/inspiration'
import { CATEGORY_LABELS } from '@/constants/tag'

/** 现网 tags 表实际出现过的类别（2026-11-16 只读盘点；删任何一项都会让对应标签显示英文） */
const LIVE_CATEGORIES = [
  'style',
  'item_type',
  'color',
  'body_part',
  'fit',
  'attribute',
  'atmosphere',
  'expression',
  'leg_posture',
  'design_detail',
  'material',
  'outfit',
]

describe('标签类别契约', () => {
  it('shared 每个类别都有中文名，且不是未翻译的原值', () => {
    for (const category of TAG_CATEGORIES) {
      const label = TAG_CATEGORY_LABELS[category]
      expect(label, `缺少 ${category} 的中文名`).toBeTruthy()
      expect(label, `${category} 的中文名不能等于原值`).not.toBe(category)
    }
  })

  it('Web 映射与 shared 类别集合完全一致（无遗漏、无多余）', () => {
    expect(Object.keys(CATEGORY_LABELS).sort()).toEqual([...TAG_CATEGORIES].sort())
  })

  it('现网实际类别全部有中文名，且不含库里不存在的 season', () => {
    for (const category of LIVE_CATEGORIES) {
      expect(CATEGORY_LABELS[category], `现网类别 ${category} 缺中文名`).toBeTruthy()
    }
    expect(TAG_CATEGORIES).not.toContain('season')
  })
})
