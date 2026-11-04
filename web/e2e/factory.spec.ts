/** E2E 数据工厂冒烟：创建 → 详情可读 → 物理清理（不污染素材库）。 */

import { expect, test } from '@playwright/test'
import { createInspiration, deleteInspiration, E2E_API_BASE, waitForInspiration } from './factories'

test('数据工厂：创建素材、详情可读、清理后 404', async ({ request }) => {
  const id = await createInspiration(request)
  try {
    await waitForInspiration(request, id)
    const detail = await request.get(`${E2E_API_BASE}/api/inspirations/${id}`)
    expect(detail.ok()).toBeTruthy()
    expect((await detail.json()).id).toBe(id)
  } finally {
    await deleteInspiration(request, id)
  }

  const gone = await request.get(`${E2E_API_BASE}/api/inspirations/${id}`)
  expect(gone.status()).toBe(404)
})
