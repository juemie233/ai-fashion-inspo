/** 本地出图（ComfyUI）API 客户端。
 *
 * 只有这里直接碰 `/api/generate/*`；组件与 composable 通过本模块，便于单测 mock。
 */

import apiClient from '@/api/client'

/** 单个出图档位的元信息（来自后端 MODEL_SPECS，前端不写死档位名与文案）。
 *
 * 不 export：只作为 {@link GenerateStatus.models_meta} 的结构使用，模块外无人引用 ——
 * 导出会被 ts-prune 判成死代码（scripts/ci/check-web-dead-code.mjs）。
 */
interface GenerateModelMeta {
  label: string
  kind: string
  defaults: Record<string, number | string>
  /** 无内容安全过滤的权重：前端据此打「未审查」标并提示风险 */
  uncensored?: boolean
  /** 风险说明文案（随档位走后端，避免前端各写一份） */
  notice?: string | null
}

/** 就绪状态（ComfyUI 是否可用、模型是否就位、Ollama 占了多少显存）。 */
export interface GenerateStatus {
  ready: boolean
  reason: string | null
  comfyui: {
    version?: string
    url?: string
    vram_total_gib?: number
    vram_free_gib?: number
  }
  models: Record<string, string>
  ollama_resident: { name: string; size_gib: number }[]
  models_meta?: Record<string, GenerateModelMeta>
}

/** 一次出图批次。 */
export interface GenerationRun {
  id: number
  kind: string
  status: string
  prompt: string
  negative: string | null
  params: Record<string, unknown>
  model_key: string
  workflow_name: string
  workflow_hash: string
  total: number
  done: number
  failed: number
  error: string | null
  task_id: number | null
  batch_dir: string
  created_at: string | null
}

/** 候选图（未入库前只存在于候选区）。 */
export interface GenerationCandidate {
  filename: string
  index: number
  seed: number
  size_bytes: number
  imported: boolean
  url: string
}

/** 任务侧信息：进度百分比 + result 里的 stage / waited_seconds。 */
export interface GenerationTaskInfo {
  id: number
  status: string
  progress: number
  result: { stage?: string; waited_seconds?: number; vram_free_gib?: number; [k: string]: unknown }
}

export interface GenerationRunDetail {
  run: GenerationRun
  items: GenerationCandidate[]
  task?: GenerationTaskInfo | null
}

export interface CreateRunPayload {
  kind?: 't2i' | 'i2i'
  prompt: string
  negative?: string
  width?: number
  height?: number
  steps?: number
  cfg?: number
  seed?: number
  count?: number
  model_key?: string
  /** 参考素材 id（i2i 必填 1~10 个） */
  reference_ids?: string[]
  /** 姿势图 id（换姿势；i2i 可选，来自姿势图库。会占一张参考额度） */
  pose_id?: string
}

/** 生成历史的一条（列表用）。
 *
 * prompt 是**完整**描述（后端不再截断），列表列用 ellipsis 收起、展开行看全文。
 */
export interface GenerationRunBrief {
  id: number
  kind: string
  status: string
  prompt: string
  negative: string | null
  /** 生成参数快照：宽高 / 步数 / CFG / 采样器 / seed / 参考素材 id 等 */
  params: Record<string, unknown>
  model_key: string
  workflow_name: string
  total: number
  done: number
  failed: number
  error: string | null
  created_at: string | null
  updated_at: string | null
  /** 候选图缩略图（最多 4 张；目录已被清理时为空数组）—— 列表用，不算哈希 */
  candidates?: GenerationRunThumb[]
  /** 该批次已入库的素材张数（后端一条聚合 SQL 得出） */
  imported_count?: number
  /** 候选目录是否已被清掉（true → 前端显示「图已清理」占位） */
  files_missing?: boolean
  favorite?: boolean
  title?: string | null
  note?: string | null
  expires_at?: string | null
}

/** 列表里的候选缩略图（不带逐张 imported：那要算哈希，只有详情接口才做） */
interface GenerationRunThumb {
  filename: string
  thumb_url: string
}

/** 生成历史查询参数（服务端分页 + 筛选） */
export interface GenerationHistoryQuery {
  page?: number
  size?: number
  /** 兼容老调法：等价于 size（后端已标注 deprecated） */
  limit?: number
  kind?: 't2i' | 'i2i'
  model_key?: string
  status?: string
  /** true 只看已入库；false 只看未入库 */
  has_imported?: boolean
  /** 关键字：描述或负面词 */
  q?: string
  /** 参考素材 id（精确命中） */
  reference_id?: string
  /** 只看已收藏 / 未收藏 */
  favorite?: boolean
  since?: string
  until?: string
}

interface GenerationHistoryPage {
  items: GenerationRunBrief[]
  /** 候选保留天数（前端显示「图还剩 N 天」） */
  keep_days?: number
  total: number
  page: number
  size: number
}

/** 批次标注的部分更新：`null` 表示「本次不改」，清空要走 clear_* */
export interface GenerationRunMetaPatch {
  favorite?: boolean
  title?: string | null
  note?: string | null
  clear_title?: boolean
  clear_note?: boolean
}

/** 近 N 天出图统计（历史区顶部数字卡） */
export interface GenerationRunStats {
  days: number
  runs: number
  images: number
  imported_images: number
  /** 入库率 = 入库张数 / 出图张数 */
  import_rate: number
  /** 平均耗时（秒）：含排队等待 */
  avg_duration_s: number
  failure_rate: number
  by_model: Array<{ model_key: string; runs: number; images: number }>
}

export interface PromptPreview {
  prompt: string
  parts: { text: string; source: string }[]
}

/**
 * 导出生成历史（CSV / JSON）并触发浏览器下载。
 *
 * 走 apiClient 而不是 `window.open`：只有走 axios 才会带上 `X-API-Key`（如果配置了），
 * 也能从 `content-disposition` 拿到后端生成的文件名（带时间戳）。
 * 筛选参数与列表一致——导出的一定是「当前筛出来的」。
 */
export async function exportRuns(
  fmt: 'csv' | 'json',
  filters: GenerationHistoryQuery = {},
): Promise<void> {
  // 导出不分页：显式剔除分页参数，避免把 page/size 传给后端（它也不认）
  const params: Record<string, unknown> = { fmt }
  for (const [key, value] of Object.entries(filters)) {
    if (key === 'page' || key === 'size' || key === 'limit' || value === undefined) continue
    params[key] = value
  }
  const res = await apiClient.get<Blob>('/generate/runs/export', {
    params,
    responseType: 'blob',
  })
  const disposition = (res.headers['content-disposition'] as string) || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match?.[1] || `generate-history-${Date.now()}.${fmt}`
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function fetchStatus(): Promise<GenerateStatus> {
  const { data } = await apiClient.get<GenerateStatus>('/generate/status')
  return data
}

export async function createRun(payload: CreateRunPayload): Promise<{
  run_id: number
  task_id: number
  seed: number
  batch_dir: string
  total: number
}> {
  const { data } = await apiClient.post('/generate/runs', payload)
  return data
}

export async function fetchRun(runId: number): Promise<GenerationRunDetail> {
  const { data } = await apiClient.get<GenerationRunDetail>(`/generate/runs/${runId}`)
  return data
}

export async function confirmImport(
  runId: number,
  filenames: string[],
): Promise<{ imported: number; skipped: number; ids: string[] }> {
  const { data } = await apiClient.post(`/generate/runs/${runId}/confirm`, { filenames })
  return data
}

export async function cancelRun(runId: number): Promise<void> {
  await apiClient.post(`/generate/runs/${runId}/cancel`)
}

export async function discardRun(runId: number): Promise<{ removed_files: number }> {
  const { data } = await apiClient.delete(`/generate/runs/${runId}`)
  return data
}

export async function previewPrompt(inspirationIds: string[]): Promise<PromptPreview> {
  const { data } = await apiClient.post<PromptPreview>('/generate/prompt/preview', {
    inspiration_ids: inspirationIds,
  })
  return data
}

/**
 * 生成历史（服务端分页 + 筛选）。
 *
 * 兼容两种调法：`listRuns(20)`（老代码）与 `listRuns({ page, size, q })`（新历史区）。
 * 后端同时接受 `limit`（旧）与 `size`（新），语义等价。
 */
export async function listRuns(
  query: number | GenerationHistoryQuery = 20,
): Promise<GenerationHistoryPage> {
  const params: GenerationHistoryQuery = typeof query === 'number' ? { limit: query } : query
  const { data } = await apiClient.get<GenerationHistoryPage>('/generate/runs', { params })
  return data
}

/** 同参数再生成（seed=0 随机；>0 复现）。 */
/** 更新批次标注（收藏 / 命名 / 备注），返回更新后的批次摘要 */
export async function updateRunMeta(
  runId: number,
  patch: GenerationRunMetaPatch,
): Promise<GenerationRunBrief> {
  const { data } = await apiClient.patch<GenerationRunBrief>(`/generate/runs/${runId}`, patch)
  return data
}

/** 让本地模型给这批起个短标题（只返回建议，不写库；模型不可用时后端回退截断） */
export async function autoTitleRun(
  runId: number,
): Promise<{ title: string; source: 'ai' | 'fallback' }> {
  const { data } = await apiClient.post<{ title: string; source: 'ai' | 'fallback' }>(
    `/generate/runs/${runId}/auto-title`,
  )
  return data
}

/** 近 N 天出图统计（days=0 表示全部历史） */
export async function fetchRunStats(days = 30): Promise<GenerationRunStats> {
  const { data } = await apiClient.get<GenerationRunStats>('/generate/runs/stats', {
    params: { days },
  })
  return data
}

export async function retryRun(
  runId: number,
  seed = 0,
): Promise<{ run_id: number; task_id: number; seed: number }> {
  const { data } = await apiClient.post(`/generate/runs/${runId}/retry`, null, {
    params: { seed },
  })
  return data
}

/** 单张换 seed 重出（新建只含 1 张的批次，不动原批次）。 */
export async function regenerateCandidate(
  runId: number,
  filename: string,
  seed = 0,
): Promise<{ run_id: number; task_id: number; seed: number; original_seed: number }> {
  const { data } = await apiClient.post(`/generate/runs/${runId}/regenerate`, null, {
    params: { filename, seed },
  })
  return data
}
