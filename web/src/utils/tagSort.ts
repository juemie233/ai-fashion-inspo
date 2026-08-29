/** 标签展示排序工具。 */

/** 可排序标签的最小结构：名称 + 类别。 */
export interface SortableTag {
  name: string
  category: string
}

/**
 * 已知标签类别的展示优先级（数字越小越靠前）。
 *
 * 前 5 个为业务明确要求的突出维度；其余为穿搭分析的其他结构化维度，
 * 按「整体观感 → 外观属性 → 具体单品 → 穿着细节 → 杂项」的次序排列，
 * 避免 leg_posture（腿部姿态）等明确维度与 attribute（属性）等杂项混排。
 * 未在此表中的未知类别归入最低优先级。
 * 旧 PascalCase 键（Atmosphere/Expression/Leg_Posture）保留同优先级，
 * 兼容历史快照数据。
 */
const CATEGORY_RANK: Record<string, number> = {
  style: 0, // 风格
  atmosphere: 1, // 氛围
  Atmosphere: 1, // 氛围（旧类别名，历史数据兜底）
  // 袜子/丝袜、鞋子按名称识别（见 NAME_RANK），可能跨 item_type/body_part
  expression: 4, // 模特表情
  Expression: 4, // 模特表情（旧类别名，历史数据兜底）
  color: 5, // 颜色
  fit: 6, // 版型
  item_type: 7, // 单品
  design_detail: 8, // 款式细节（袖型/领型/口袋等结构性设计）
  material: 9, // 面料（针织/牛仔/蕾丝等材质质感）
  body_part: 10, // 穿着方式（遗留类别：袖长/领型/露肤等存量）
  leg_posture: 11, // 腿部姿态
  Leg_Posture: 11, // 腿部姿态（旧类别名，历史数据兜底）
  attribute: 12, // 属性（露脸/全身/站姿等杂项）
  outfit: 13, // 穿搭大标签
  free: 14, // 自定义
}

/**
 * 按名称识别的标签优先级（跨类别，覆盖类别默认值）。
 *
 * 袜子/丝袜、鞋子可能落在 item_type（单品）或 body_part（穿着方式）等不同
 * 类别下，无法仅凭 category 判断，故按名称关键字识别并提到前面。
 */
const NAME_RANK: Array<{ rank: number; match: (name: string) => boolean }> = [
  { rank: 2, match: (name) => name.includes('袜') },
  { rank: 3, match: (name) => name.includes('鞋') },
]

/** 未知类别（含自定义新增类别）的兜底优先级。 */
const DEFAULT_RANK = 99

/**
 * 计算单个标签的展示优先级。
 *
 * 名称规则优先于类别规则（袜/鞋需提到固定位置，即使其类别是 item_type）；
 * 同一标签不会被重复归类。
 */
function priorityOf(tag: SortableTag): number {
  for (const rule of NAME_RANK) {
    if (rule.match(tag.name)) return rule.rank
  }
  return CATEGORY_RANK[tag.category] ?? DEFAULT_RANK
}

/**
 * 按业务优先级对标签数组排序，返回新数组（不修改入参）。
 *
 * 同一优先级内的标签保持原有相对顺序（依赖 ES2019 起 Array.prototype.sort 的
 * 稳定性，现代浏览器/Node 均已保证）。
 */
export function sortAnalysisTags<T extends SortableTag>(tags: T[]): T[] {
  return tags
    .map((tag, index) => ({ tag, index, rank: priorityOf(tag) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.tag)
}
