/**
 * E2E 测试数据工厂（2026-11-04 测试体系评测 P2）。
 *
 * E2E 默认只读，不造数；需要写链路时用这里通过后端 API 创建最小素材，
 * 测试结束在 finally 里物理删除，避免污染真实素材库。
 * 与 backend/tests/factories.py 保持同一组「素材」最小字段口径。
 */

import type { APIRequestContext } from '@playwright/test'

export const E2E_API_BASE = process.env.E2E_API_BASE || 'http://127.0.0.1:18888'

/** 64×64 纯色 PNG：足够通过上传校验，不需要额外图片库。 */
const E2E_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAgUlEQVR4nNXOQREAIAzAsFL/IhCBCGQhYg+uUZB1z6ZM4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iRO4iTO34GpBwneApzlqIn/AAAAAElFTkSuQmCC'

/** 创建一条最小素材，返回 inspiration id。 */
export async function createInspiration(request: APIRequestContext): Promise<string> {
  const resp = await request.post(`${E2E_API_BASE}/api/inspirations`, {
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
  const trashed = await request.post(`${E2E_API_BASE}/api/inspirations/${id}/trash`)
  if (!trashed.ok()) {
    throw new Error(`移入垃圾桶失败：HTTP ${trashed.status()} ${await trashed.text()}`)
  }
  const resp = await request.delete(`${E2E_API_BASE}/api/inspirations/${id}`)
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
