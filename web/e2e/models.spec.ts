/** AI 模型管理核心链路：质量审核统计卡片与标签分析队列。 */

import { expect, test } from '@playwright/test'

test('质量审核统计卡片渲染（含通过率）', async ({ page }) => {
  await page.goto('/models?tab=review')
  await expect(page.getByText('待审核').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('通过率').first()).toBeVisible()
})

test('分析质量统计卡片渲染（含平均耗时）', async ({ page }) => {
  await page.goto('/models?tab=quality')
  await expect(page.getByText('素材总数').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('平均耗时').first()).toBeVisible()
})
