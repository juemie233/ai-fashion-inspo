/** 数据治理（/admin/governance）：统计渲染 + 子页面 URL + 危险操作二次确认。

 * **不真的执行删除**：这里的按钮是物理删文件/素材。e2e 只验证
 * 「点得开 → 弹确认 → 取消后一个删除请求都不发」，真正执行的语义由后端单测覆盖
 * （清理孤立文件保护期、批量删除条件口径、去重任务）。
 */

import { expect, test } from '@playwright/test'

test('治理页：统计与四个子页面切换（tab 写进 URL）', async ({ page }) => {
  await page.goto('/admin/governance')

  await expect(page.getByRole('heading', { name: '数据治理' })).toBeVisible()
  // 批量清理页的无标签/分析失败统计（数字来自后端）
  await expect(page.getByText('无标签素材').first()).toBeVisible({ timeout: 15_000 })

  // 子页面切换：每个 tab 都要反映到 URL（刷新后停留原页）
  for (const [label, param] of [
    ['数据完整性', 'integrity'],
    ['重复文件', 'duplicates'],
    ['近似重复', 'neardup'],
  ] as const) {
    await page.locator('.arco-tabs-tab', { hasText: label }).first().click()
    await expect(page).toHaveURL(new RegExp(`tab=${param}`))
    await expect(page.locator('.arco-tabs-tab-active').first()).toHaveText(label)
  }

  // 回到默认页：tab 参数应从 URL 消失
  await page.locator('.arco-tabs-tab', { hasText: '批量清理' }).first().click()
  await expect(page).not.toHaveURL(/tab=/)
})

test('治理页：数据完整性检查可执行且给出结论', async ({ page }) => {
  await page.goto('/admin/governance?tab=integrity')

  await expect(page.getByText('数据完整性检查').first()).toBeVisible({ timeout: 15_000 })
  // 进页即会跑一次检查：结论只有「完整 / 有问题」两种，出现任一都说明后端真的返回了
  await expect(page.getByText(/数据完整，未发现|发现.*问题|缺失文件|孤立文件/).first()).toBeVisible(
    { timeout: 30_000 },
  )

  // 手动重跑：点「重新检查」后结论应重新出现（中途是 loading 态，所以放宽超时）
  await page.getByRole('button', { name: '重新检查' }).first().click()
  await expect(page.getByText(/数据完整，未发现|发现.*问题|缺失文件|孤立文件/).first()).toBeVisible(
    { timeout: 30_000 },
  )
  await expect(page.getByText('页面渲染出错')).toHaveCount(0)
})

test('治理页：批量删除要二次确认，取消后不发删除请求', async ({ page }) => {
  const deleteRequests: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/admin/batch-delete')) {
      deleteRequests.push(req.url())
    }
  })

  await page.goto('/admin/governance')
  const dangerButton = page.getByRole('button', { name: '批量删除' }).first()
  await expect(dangerButton).toBeVisible({ timeout: 15_000 })
  await dangerButton.click()

  // 破坏性操作必须弹确认（Arco popconfirm/modal）
  await expect(page.locator('.arco-popconfirm, .arco-modal').first()).toBeVisible({
    timeout: 5000,
  })
  await page
    .locator('.arco-popconfirm, .arco-modal')
    .getByRole('button', { name: '取消' })
    .first()
    .click()
  await page.waitForTimeout(500)

  expect(deleteRequests).toEqual([])
})
