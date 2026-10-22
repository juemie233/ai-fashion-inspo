/** 本地出图（ComfyUI）API 客户端。
 *
 * 只有这里直接碰 `/api/generate/*`；组件与 composable 通过本模块，便于单测 mock。
 */

import apiClient from '@/api/client'

/** 单个出图档位的元信息（来自后端 MODEL_SPECS，前端不写死档位名与文案）。
 *
 * 不 export：只作为 {@link GenerateStatus.models_meta} 的结构使用，模块外无人引用 ——
 * 导出会被 ts-prune 判成死代码（scripts/check-web-dead-code.mjs）。
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
}

/** 生成历史的一条（列表用）。 */
export interface GenerationRunBrief {
  id: number
  kind: string
  status: string
  prompt: string
  model_key: string
  total: number
  done: number
  failed: number
  error: string | null
  created_at: string | null
}

export interface PromptPreview {
  prompt: string
  parts: { text: string; source: string }[]
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

export async function listRuns(limit = 20): Promise<{ items: GenerationRunBrief[] }> {
  const { data } = await apiClient.get('/generate/runs', { params: { limit } })
  return data
}

/** 同参数再生成（seed=0 随机；>0 复现）。 */
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
