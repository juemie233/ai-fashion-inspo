/** 素材库首页核心链路：加载、筛选栏、网格/空态渲染。 */

import { expect, test } from '@playwright/test'

test('素材库首页加载并显示筛选栏', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '素材库' })).toBeVisible()
  // 来源筛选栏存在
  await expect(page.locator('.filter-bar')).toBeVisible()
  // 网格或空态至少渲染其一（页面不卡在加载态）
  await page.waitForTimeout(3000)
  const grid = page.locator('.image-grid, .masonry-grid, .home-grid, .waterfall')
  const empty = page.locator('.arco-empty')
  const hasContent = (await grid.count()) > 0 || (await empty.count()) > 0
  expect(hasContent).toBeTruthy()
})

test('素材库批量操作栏入口可用', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '批量选择' })).toBeVisible()
  await expect(page.getByRole('button', { name: '批量审核' })).toBeVisible()
})
