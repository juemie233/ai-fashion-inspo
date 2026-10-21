/** 本地出图（ComfyUI）API 客户端。
 *
 * 只有这里直接碰 `/api/generate/*`；组件与 composable 通过本模块，便于单测 mock。
 */

import apiClient from '@/api/client'

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
  models_meta?: Record<
    string,
    { label: string; kind: string; defaults: Record<string, number | string> }
  >
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

export interface GenerationRunDetail {
  run: GenerationRun
  items: GenerationCandidate[]
}

export interface CreateRunPayload {
  kind?: 't2i'
  prompt: string
  negative?: string
  width?: number
  height?: number
  steps?: number
  cfg?: number
  seed?: number
  count?: number
  model_key?: string
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
