/** 手机图剪裁结果的数据结构与安全归一化（/admin/crop-phone-screenshots/apply 返回结构）。 */

/** 内容重复对比条目：裁剪结果 vs 库中重复素材，由用户决定保留哪一张 */
export interface CropDuplicate {
  id: string
  dup_id: string
  dup_file_path?: string | null
  dup_thumbnail_path?: string | null
  dup_created_at?: string | null
  preview_path?: string | null
  reason: string
}

/** 裁剪执行结果（与后端 apply_crops 返回结构对齐） */
export interface CropApplyResult {
  processed: number
  skipped: Array<{
    id: string
    reason: string
    file_path?: string | null
    thumbnail_path?: string | null
    created_at?: string | null
  }>
  duplicates: CropDuplicate[]
  backup_dir: string | null
  vector_task_id: number | null
}

/**
 * 规整后端裁剪结果：duplicates / skipped 保证为数组、其余字段给默认值。
 *
 * 后端约定恒返回完整结构（duplicates 空时也是 []），但接口字段一旦缺失或为
 * null，前端若直接访问 `data.duplicates.length` 会抛 TypeError 并落入 catch，
 * 导致「内容重复对比弹窗不弹出」这类难以定位的问题。所有消费裁剪结果的
 * 入口都应先经本函数归一化，保证渲染与判断逻辑安全。
 */
export function normalizeApplyResult(data: unknown): CropApplyResult {
  const d = (data ?? {}) as Record<string, unknown>
  return {
    processed: typeof d.processed === 'number' ? d.processed : 0,
    skipped: Array.isArray(d.skipped) ? (d.skipped as CropApplyResult['skipped']) : [],
    duplicates: Array.isArray(d.duplicates) ? (d.duplicates as CropDuplicate[]) : [],
    backup_dir: typeof d.backup_dir === 'string' ? d.backup_dir : null,
    vector_task_id: typeof d.vector_task_id === 'number' ? d.vector_task_id : null,
  }
}
