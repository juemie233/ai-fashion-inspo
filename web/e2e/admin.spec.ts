/** 数据洞察核心链路：操作审计日志表格。 */

import { expect, test } from '@playwright/test'

test('操作审计日志表格渲染', async ({ page }) => {
  await page.goto('/admin/insights')
  await expect(page.getByText('操作审计日志').first()).toBeVisible()
  await expect(page.locator('.arco-table-th').first()).toBeVisible()
  // 时间列内容为 nowrap（单行不换行）：渲染的 span 带 white-space 内联样式
  const nowrapSpan = page
    .locator('.arco-table-tr td:first-child span[style*="white-space"]')
    .first()
  if ((await nowrapSpan.count()) > 0) {
    await expect(nowrapSpan).toHaveCSS('white-space', 'nowrap')
  }
})
