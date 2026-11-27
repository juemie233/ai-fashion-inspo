/**
 * E2E 测试数据工厂（2026-11-04 测试体系评测 P2）。
 *
 * E2E 默认只读，不造数；需要写链路时用这里通过后端 API 创建最小素材，
 * 测试结束在 finally 里物理删除，避免污染真实素材库。
 * 与 backend/tests/factories.py 保持同一组「素材」最小字段口径。
 *
 * 鉴权（2026-11-27 / task-30）：后端启用 API_KEY 时，破坏性接口
 * （`DELETE /api/inspirations/{id}` 等）必须带 `X-API-Key`，否则 401。
 * - 密钥来源：优先环境变量 `E2E_API_KEY`；`VITE_API_KEY` 仅作为本机排查的回退
 *   （Vite 约定变量，不保证与后端 API_KEY 一致）。密钥只放请求头，
 *   **不打印、不写入仓库/日志/测试报告**；
 * - 未配置密钥且探测到后端要求鉴权（401）→ 用 `test.skip()` 明确跳过写链路用例
 *   （中文原因），而不是伪装成失败；读链路用例不经过本工厂，保持运行；
 * - 已配置密钥但后端仍返回 401/403（密钥无效、后端未加载，或访问来源被回环限制
 *   拒绝）→ 明确失败并指引排查，错误信息不回显密钥内容。
 */

import { test } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'

export const E2E_API_BASE = process.env.E2E_API_BASE || 'http://127.0.0.1:18888'

/** 64×64 纯色 PNG：足够通过上传校验，不需要额外图片库。 */
const E2E_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAgUlEQVR4nNXOQREAIAzAsFL/IhCBCGQhYg+uUZB1z6ZM4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iTO34GpBwneApzlqIn/AAAAAElFTkSuQmCC'

/** 写链路密钥：优先 E2E_API_KEY；VITE_API_KEY 仅作本地排查回退。 */
const E2E_API_KEY = (process.env.E2E_API_KEY || process.env.VITE_API_KEY || '').trim()

/** 写链路请求头：配置了密钥才带 X-API-Key（后端未启用鉴权时未配置密钥仍可写）。 */
function authHeaders(): Record<string, string> {
  return E2E_API_KEY ? { 'X-API-Key': E2E_API_KEY } : {}
}

/**
 * 探测后端是否要求 X-API-Key：DELETE 一个不存在的素材 ID。
 *
 * 该请求本身没有任何数据副作用（不存在则 404、未授权则 401、密钥无效则 403），
 * 只用于在创建数据之前判断「这次环境能不能跑写链路」，避免先造出数据却删不掉。
 *
 * 返回 true 表示「后端要求鉴权且本次未携带有效密钥」。
 */
async function requiresApiKey(request: APIRequestContext): Promise<boolean> {
  const resp = await request.delete(`${E2E_API_BASE}/api/inspirations/e2e-auth-probe-no-such-id`, {
    headers: authHeaders(),
  })
  const status = resp.status()
  if (status === 404) return false // 鉴权通过（或后端未启用鉴权），写链路可继续
  if (status === 403) {
    // 密钥无效/被拒：必须在创建数据前失败，否则会造出删不掉的残留
    throw new Error(
      'E2E_API_KEY 被后端拒绝（HTTP 403：密钥无效）；请在创建任何测试数据前修正环境，' +
        '核对后端 .env 的 API_KEY（不要把密钥内容贴进日志/报告）。',
    )
  }
  if (status !== 401) {
    throw new Error(
      `E2E 写链路鉴权探测返回意外状态 HTTP ${status}（期望 404/401/403）：` +
        '请检查后端是否在运行、E2E_API_BASE 是否正确；为避免产生无法清理的数据，本次不创建测试素材。',
    )
  }
  if (E2E_API_KEY) {
    // 配了密钥仍 401：环境配置错误，明确失败指引排查（不回显密钥内容）
    throw new Error(
      'E2E_API_KEY 已配置但后端仍返回 401：密钥无效、后端未加载该密钥，' +
        '或当前访问来源被回环限制拒绝；请核对后端 .env 的 API_KEY 与 E2E_API_KEY 是否一致' +
        '（不要把密钥内容贴进日志/报告）。',
    )
  }
  return true
}

/** 同一次 worker 内只探测一次（多项目/多用例复用，避免每个请求都多打一次探测）。 */
let authProbe: Promise<boolean> | null = null

/** 写链路入口守卫：无密钥 + 后端要求鉴权 → 跳过当前用例（中文原因），不假失败。 */
async function ensureWriteAuth(request: APIRequestContext): Promise<void> {
  authProbe ??= requiresApiKey(request)
  if (await authProbe) {
    test.skip(
      true,
      '后端要求 X-API-Key（或当前访问来源被鉴权拒绝）且未配置 E2E_API_KEY：' +
        '写链路用例跳过；配置 E2E_API_KEY 后重跑，读链路用例不受影响',
    )
  }
}

/** 创建一条最小素材，返回 inspiration id（写链路入口，会先做鉴权探测）。 */
export async function createInspiration(request: APIRequestContext): Promise<string> {
  await ensureWriteAuth(request)
  const resp = await request.post(`${E2E_API_BASE}/api/inspirations`, {
    headers: authHeaders(),
    multipart: {
      file: {
        name: 'e2e-factory.png',
        mimeType: 'image/png',
        buffer: Buffer.from(E2E_PNG_BASE64, 'base64'),
      },
      source_type: 'manual_upload',
      source_author: 'E2E 数据工厂',
    },
  })
  if (!resp.ok()) throw new Error(`创建 E2E 素材失败：HTTP ${resp.status()} ${await resp.text()}`)
  const body = (await resp.json()) as { id: string }
  return body.id
}

/** 物理删除工厂素材（先软删入垃圾桶，再彻底删除；只用于清理自己创建的数据）。 */
export async function deleteInspiration(request: APIRequestContext, id: string): Promise<void> {
  const trashed = await request.post(`${E2E_API_BASE}/api/inspirations/${id}/trash`, {
    headers: authHeaders(),
  })
  if (!trashed.ok()) {
    throw new Error(`移入垃圾桶失败：HTTP ${trashed.status()} ${await trashed.text()}`)
  }
  const resp = await request.delete(`${E2E_API_BASE}/api/inspirations/${id}`, {
    headers: authHeaders(),
  })
  if (!resp.ok()) throw new Error(`清理 E2E 素材失败：HTTP ${resp.status()} ${await resp.text()}`)
}

/** 等刚创建的素材对详情接口可见（上传提交与读请求之间可能有毫秒级竞态）。 */
export async function waitForInspiration(
  request: APIRequestContext,
  id: string,
  attempts = 10,
): Promise<void> {
  for (let i = 0; i < attempts; i += 1) {
    const resp = await request.get(`${E2E_API_BASE}/api/inspirations/${id}`)
    if (resp.ok()) return
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`素材 ${id} 在 ${attempts * 100}ms 内仍不可见`)
}
