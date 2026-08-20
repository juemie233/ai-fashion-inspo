/** 采集管理核心链路：任务表格、结果预览展开、六列网格。 */

import { expect, test } from '@playwright/test'

test('采集任务历史表格渲染', async ({ page }) => {
  await page.goto('/scraper')
  await expect(page.getByText('采集任务历史').first()).toBeVisible()
  await expect(page.locator('.arco-table-th').first()).toBeVisible()
  const rows = page.locator('.arco-table-tr')
  await expect(rows.first()).toBeVisible({ timeout: 10_000 })
})

test('结果预览展开且网格为六列', async ({ page }) => {
  await page.goto('/scraper')
  await expect(page.getByText('采集任务历史').first()).toBeVisible()
  // 点击第一行的「结果」按钮
  const resultBtn = page.locator('.arco-table-tr button', { hasText: /^结果$/ }).first()
  if ((await resultBtn.count()) > 0) {
    await resultBtn.click()
    const grid = page.locator('.results-grid')
    await expect(grid.first()).toBeVisible({ timeout: 10_000 })
    const cols = await grid
      .first()
      .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)
    expect(cols).toBe(6)
  } else {
    // 无任务结果按钮时跳过（无结果任务）
    test.skip()
  }
})
