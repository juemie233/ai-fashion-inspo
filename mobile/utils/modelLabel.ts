/** 出图档位展示名工具。
 *
 * 展示名的单一来源是后端 `/generate/models`（MODEL_SPECS）；这里的内置表只是
 * 「接口暂时拉不到」时的兜底，避免新增档位时手机端显示原始 key（英文）。
 */

/** 兜底展示名：仅覆盖当前已知档位 */
export const FALLBACK_MODEL_LABELS: Record<string, string> = {
  qwen_2_1_i2i: 'Qwen 2.1 图生图',
  qwen_2_1_uc_i2i: 'Qwen 2.1 图生图（未审查）',
  z_image_int8: 'Z-Image',
}

/** 去掉后端 label 里的补充说明（与 Web 的 shortModelLabel 同口径） */
export function shortModelLabel(label: string): string {
  return label.split('（')[0] ?? label
}

/** 解析展示名：后端 labels > 内置兜底 > 原始 key */
export function modelLabel(
  key: string | null | undefined,
  labels: Record<string, string>,
): string {
  if (!key) return '本地出图'
  return labels[key] ?? FALLBACK_MODEL_LABELS[key] ?? key
}
