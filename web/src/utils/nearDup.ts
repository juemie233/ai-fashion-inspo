/** 近似重复组决策的纯函数：按用户选择计算应删除的素材 ID（与组件解耦，便于单测）。 */

import type { NearDuplicateGroup } from '@/api/admin'

/** 用户对某近似重复组的保留决策 */
export type DupDecision = 'keep-left' | 'keep-right' | 'skip' | 'delete-both'

/**
 * 计算某组在给定决策下应删除的素材 ID 列表。
 *
 * - keep-left：保留组内第一张（左图），删除其余全部
 * - keep-right：保留组内第二张（右图），删除其余全部（含左图）
 * - skip：都保留，删除列表为空
 * - delete-both：两张都不满意，删除当前对比的前两张（组内其余暂不处理）
 *
 * @param group 近似重复组（files 至少 2 张，顺序为评分降序）
 * @param decision 保留决策
 * @returns 应删除的素材 ID 列表
 */
export function collectIdsToDelete(
  group: NearDuplicateGroup,
  decision: DupDecision,
): string[] {
  const files = group.files ?? []
  if (decision === 'skip') {
    return []
  }
  if (decision === 'keep-left') {
    return files.slice(1).map((f) => f.id)
  }
  if (decision === 'delete-both') {
    return files.slice(0, 2).map((f) => f.id)
  }
  // keep-right：保留第二张，删除其余（含第一张）
  const rightId = files[1]?.id
  return files.filter((f) => f.id !== rightId).map((f) => f.id)
}
