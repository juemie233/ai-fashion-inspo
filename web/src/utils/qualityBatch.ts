/** 批量质量审核的共享常量与耗时估算。
 *
 * 上限与后端 `GET /api/ai/quality-check` 的 `le=5000` 对齐：批量导入（f2 抖音素材）
 * 后常有上万条待审核，200 的上限需要点几十次；而上限放宽后单任务可能跑十几小时，
 * 因此执行器已支持在任务管理页暂停/取消（每批检查一次状态）。
 *
 * 耗时按实测 12.5 秒/张（`ai_analysis_log` 平均）估算；实际并发由后端
 * `analyze_concurrency` 决定（当前默认 1，即串行）。
 */

/** 单次批量审核的素材数上限（与后端校验保持一致，避免前端放行、后端 422） */
export const QUALITY_BATCH_MAX = 5000

/** 单张审核的实测平均耗时（秒） */
export const QUALITY_SEC_PER_ITEM = 12.5

/** 多数入口的默认提交量（不填数量时使用） */
export const QUALITY_BATCH_DEFAULT = 200

/**
 * 估算审核耗时（小时，保留一位小数）。
 *
 * @param count 素材数量
 * @param concurrency 后端并发数（默认 1，与 settings.analyze_concurrency 默认值一致）
 * @returns 小时数；count 非法时返回 0
 */
export function estimateQualityHours(
  count: number,
  concurrency = 1,
  /** 单张实测平均耗时（秒）；不传则用兜底常量。由 /ai/quality-stats 的 avg_seconds 提供 */
  secPerItem?: number,
): number {
  if (!Number.isFinite(count) || count <= 0) return 0
  const parallel = concurrency > 0 ? concurrency : 1
  const per = secPerItem && secPerItem > 0 ? secPerItem : QUALITY_SEC_PER_ITEM
  return Math.round(((count * per) / parallel / 3600) * 10) / 10
}

/**
 * 生成「N 条 · 预计 X 小时」的提示文案（用于按钮 tooltip 与提交后的通知）。
 *
 * @param count 素材数量
 * @param concurrency 后端并发数
 * @returns 展示文案
 */
export function qualityBatchHint(count: number, concurrency = 1, secPerItem?: number): string {
  const hours = estimateQualityHours(count, concurrency, secPerItem)
  if (hours <= 0) return '0 条'
  const per = secPerItem && secPerItem > 0 ? secPerItem : QUALITY_SEC_PER_ITEM
  // 实测值来自最近成功的审核日志；没有数据时退回兜底常量，文案里标明来源避免误导
  const source = secPerItem && secPerItem > 0 ? '实测均值' : '兜底估值'
  return `${count} 条 · 预计约 ${hours} 小时（${per} 秒/张，${source}；可在任务管理页暂停/取消）`
}
