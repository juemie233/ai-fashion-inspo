/** 任务管理核心链路：表格渲染、状态标签、分页。 */

import { expect, test } from '@playwright/test'

test('任务管理表格渲染正常（不再停留加载态）', async ({ page }) => {
  await page.goto('/tasks')
  await expect(page.getByText('任务管理').first()).toBeVisible()
  // 表格渲染（表头 + 至少一行数据）
  await expect(page.locator('.arco-table-th').first()).toBeVisible()
  const rows = page.locator('.arco-table-tr')
  await expect(rows.first()).toBeVisible({ timeout: 10_000 })
  // 状态列有状态标签（StatusTag：arco-tag）
  const firstTag = page.locator('.arco-table-tr .arco-tag').first()
  await expect(firstTag).toBeVisible()
  // 页面无渲染错误（错误边界占位不出现）
  await expect(page.getByText('页面渲染出错')).toHaveCount(0)
})

test('任务管理刷新按钮可用', async ({ page }) => {
  await page.goto('/tasks')
  await expect(page.getByRole('button', { name: '刷新' })).toBeVisible()
})
