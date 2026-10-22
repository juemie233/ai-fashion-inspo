/** 采集管理核心链路：任务表格、结果预览展开、六列网格。 */

import { expect, test } from '@playwright/test'

test('采集任务历史表格渲染', async ({ page }) => {
  await page.goto('/scraper')
  // 断言表格本身（原「采集任务历史」标题已随页面改版移除；页签 class 随 Arco 版本变化，
  // 用表头文案这种语义稳定的锚点更可靠）
  await expect(page.locator('.arco-table-th').first()).toHaveText('任务ID')
  const rows = page.locator('.arco-table-tr')
  await expect(rows.first()).toBeVisible({ timeout: 10_000 })
})

test('结果预览展开且网格为六列', async ({ page }) => {
  await page.goto('/scraper')
  await expect(page.locator('.arco-table-th').first()).toHaveText('任务ID')
  // CDP 表格行内的按钮文案是「结果」（f2 通道的历史表是「查看结果」，对应另一个面板）；
  // 用 exact 精确匹配避免点到「查看结果」。先等按钮出现再点——表格数据是接口返回的，
  // 直接 count() 不等待会误判成「没有结果按钮」而跳过。
  const resultBtn = page.getByRole('button', { name: '结果', exact: true }).first()
  await expect(resultBtn).toBeVisible({ timeout: 10_000 })
  await resultBtn.click()

  const grid = page.locator('.results-grid')
  await expect(grid.first()).toBeVisible({ timeout: 10_000 })
  const cols = await grid
    .first()
    .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)
  expect(cols).toBe(6)
})
