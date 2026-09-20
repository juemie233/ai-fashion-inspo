/** 近似重复组决策的纯函数：按用户选择计算应删除的素材 ID（与组件解耦，便于单测）。 */

import type { NearDuplicateGroup, NearDuplicateResult } from '@/api/admin'

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
export function collectIdsToDelete(group: NearDuplicateGroup, decision: DupDecision): string[] {
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

/**
 * 扫描范围文案：区分「随机抽样」「全库扫描」「缓存未就绪只扫了已缓存部分」三种口径。
 *
 * 为什么要单独判定第三种：扫描接口只做**限时**补算，大库首扫时缓存可能只有一半，
 * 此时把结果说成「随机扫描 N / M 张」会误导（它不是抽样，是缓存没补齐），
 * 说成「全库扫描」更是谎报覆盖率。
 *
 * @param result 扫描结果（scanned/cached_total/missing/truncated）
 * @param limit 本次请求的 limit（0 = 全库）
 * @returns 中文范围描述
 */
export function nearDupScopeLabel(
  result: Pick<NearDuplicateResult, 'scanned' | 'total' | 'truncated' | 'missing'>,
  limit: number,
): string {
  if (result.missing > 0) {
    return `已扫描已缓存的 ${result.scanned} 张（哈希缓存还差 ${result.missing} 张未补齐）`
  }
  if (limit > 0 || result.truncated) {
    return `随机扫描 ${result.scanned} / ${result.total} 张`
  }
  return `全库扫描 ${result.scanned} 张`
}
