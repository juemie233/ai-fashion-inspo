import { describe, expect, it } from 'vitest'
import { sortAnalysisTags } from '../tagSort'

/** 构造标签的简写 */
function t(name: string, category = 'free') {
  return { name, category }
}

describe('sortAnalysisTags', () => {
  it('按优先级排列：风格 > 氛围 > 袜 > 鞋 > 模特表情 > 其余维度', () => {
    const tags = [
      t('尖头高跟鞋', 'item_type'), // 鞋
      t('微笑', 'Expression'), // 模特表情
      t('法式', 'style'), // 风格
      t('连衣裙', 'item_type'), // 单品
      t('纯欲氛围', 'Atmosphere'), // 氛围
      t('过膝白袜', 'body_part'), // 袜
    ]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    expect(names).toEqual(['法式', '纯欲氛围', '过膝白袜', '尖头高跟鞋', '微笑', '连衣裙'])
  })

  it('丝袜/船袜等含「袜」标签归入袜子优先级，先于鞋类', () => {
    const tags = [t('船袜'), t('黑丝连裤袜'), t('小白鞋')]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    expect(names).toEqual(['船袜', '黑丝连裤袜', '小白鞋'])
  })

  it('穿搭维度类别按确定次序排列，腿部姿态排在属性之前', () => {
    // 回归：此前 attribute（如「七分」）与 Leg_Posture 同属兜底优先级，
    // 按原始顺序混排；现在各维度有确定次序。
    const tags = [
      t('微笑', 'Expression'),
      t('七分', 'attribute'),
      t('交叉腿', 'Leg_Posture'),
      t('白色', 'color'),
      t('修身', 'fit'),
    ]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    // 表情 → 颜色 → 版型 → 腿部姿态 → 属性
    expect(names).toEqual(['微笑', '白色', '修身', '交叉腿', '七分'])
  })

  it('同一优先级内保持原有相对顺序（稳定排序）', () => {
    const tags = [t('日系', 'style'), t('韩系', 'style'), t('甜妹', 'style'), t('红色', 'color')]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    expect(names).toEqual(['日系', '韩系', '甜妹', '红色'])
  })

  it('不修改入参数组（返回新数组）', () => {
    const tags = [t('鞋', 'item_type'), t('风格', 'style')]
    const original = [...tags]
    sortAnalysisTags(tags)
    expect(tags).toEqual(original)
    // 入参顺序未变
    expect(tags[0].name).toBe('鞋')
  })

  it('空数组返回空数组', () => {
    expect(sortAnalysisTags([])).toEqual([])
  })

  it('Expression 类别即使名称不含表情词也按模特表情优先级处理', () => {
    const tags = [t('某个属性', 'attribute'), t('冷脸', 'Expression')]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    expect(names).toEqual(['冷脸', '某个属性'])
  })

  it('未知类别归入最低优先级，排在所有已知维度之后', () => {
    const tags = [t('未知维度标签', 'new_category'), t('法式', 'style'), t('红色', 'color')]
    const names = sortAnalysisTags(tags).map((x) => x.name)
    expect(names).toEqual(['法式', '红色', '未知维度标签'])
  })
})
