/** 质量审核「结构化拒绝原因」的中文标签与筛选项。
 *
 * 后端 `quality_reason_code` 是枚举（见 `quality.py` 的 REASON_CODES），自由文本
 * `quality_reason` 仍是给人看的细节；两者互补，这里只负责把枚举转成 UI 文案。
 */

/** 原因码 → 中文标签（含初筛器专用码） */
const QUALITY_REASON_LABELS: Record<string, string> = {
  not_outfit: '不是穿搭',
  single_item: '只有单品特写',
  partial: '穿搭被裁切',
  low_quality: '模糊/遮挡',
  ad: '广告/文字/截图',
  other: '其它原因',
  prefilter_reject: '初筛器判定垃圾',
  unknown: '未分类',
}

/** 下拉筛选项（value 为空串表示「全部原因」） */
export const QUALITY_REASON_OPTIONS: { label: string; value: string }[] = [
  { label: '全部原因', value: '' },
  ...Object.entries(QUALITY_REASON_LABELS).map(([value, label]) => ({
    label,
    value,
  })),
]

/**
 * 把原因码转成中文标签；未知码原样返回（便于发现后端新增枚举未同步）。
 *
 * @param code 原因码，可为 null/空
 * @returns 中文标签；空值返回空串
 */
export function qualityReasonLabel(code: string | null | undefined): string {
  if (!code) return ''
  return QUALITY_REASON_LABELS[code] ?? code
}
