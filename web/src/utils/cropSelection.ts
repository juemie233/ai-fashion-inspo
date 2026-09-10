/**
 * 手机图剪裁：扫描候选的默认勾选判定（纯函数，便于单测覆盖各模式口径）。
 *
 * 口径：
 * - **content（抖音截图「内容边界检测」）默认全选**：候选均已通过检测筛选
 *   （系统 UI 特征 / 状态栏字形证据），大概率都该裁剪；默认全选后用户只需
 *   在网格中取消个别误检，免逐张手动勾选；
 * - auto（小红书截图黑边检测）：沿用后端 `auto_checked` 决策（仅双侧黑边形态
 *   勾选；单侧「黑边」多为播放器条或照片暗部，保留为候选但不默认勾选）；
 * - 旧响应无 `auto_checked` 字段时回退历史规则（排除 low 置信度与 plain 边界）。
 */

/** 勾选判定所需字段（结构类型，AdminPhoneCrop 的 CropCandidate 天然兼容） */
export interface CropSelectionCandidate {
  id: string
  auto_ok: boolean
  /** 后端勾选决策（字形证据口径）；旧响应可能缺失 */
  auto_checked?: boolean | null
  confidence?: 'high' | 'medium' | 'low' | null
  boundary_kind?: 'gray_band' | 'status_bar' | 'plain' | 'glyph_only' | null
  /** 建议裁剪的顶部比例（旧响应回退判据：>0 视为有检测信号） */
  crop_top: number
}

/** 裁剪模式：auto=小红书黑边 / content=抖音内容边界 / ratio=固定比例 */
export type CropMode = 'auto' | 'ratio' | 'content'

/** 计算扫描结果的默认勾选集合。 */
export function defaultCheckedIds<T extends CropSelectionCandidate>(
  items: T[],
  mode: CropMode,
): Set<string> {
  const ids = new Set<string>()

  // 抖音截图内容边界检测：列表内候选整体可信 → 默认全选（可逐张取消误检）
  if (mode === 'content') {
    for (const item of items) ids.add(item.id)
    return ids
  }

  for (const item of items) {
    if (item.auto_ok) {
      // 后端勾选决策优先（auto 模式仅双侧黑边勾选；content 走上面的全选分支）
      if (typeof item.auto_checked === 'boolean') {
        if (item.auto_checked) ids.add(item.id)
        continue
      }
      // 旧响应回退：低置信与 plain 边界不默认勾选
      if (item.confidence === 'low') continue
      if (item.boundary_kind === 'plain') continue
      ids.add(item.id)
      continue
    }
    // 疑似状态栏残留：采用后端决策；旧响应按「有建议裁剪比例」兜底
    if (typeof item.auto_checked === 'boolean') {
      if (item.auto_checked) ids.add(item.id)
      continue
    }
    if (item.crop_top > 0) ids.add(item.id)
  }
  return ids
}
