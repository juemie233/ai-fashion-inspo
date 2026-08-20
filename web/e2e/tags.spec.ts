/** 标签管理核心链路：展开分组、选择标签、右侧素材网格。 */

import { expect, test } from '@playwright/test'

test('标签管理展开分组并查看关联素材', async ({ page }) => {
  await page.goto('/tags')
  await expect(page.getByText('标签管理').first()).toBeVisible()
  // 展开第一个分组（a-collapse 默认折叠）
  const header = page.locator('.arco-collapse-item-header').first()
  await header.click()
  await expect(page.locator('.tag-row').first()).toBeVisible({ timeout: 10_000 })
  // 点击第一个有使用次数的标签，右侧应出现素材网格或空态
  const rows = page.locator('.tag-row')
  const count = await rows.count()
  let clicked = false
  for (let i = 0; i < Math.min(count, 30); i++) {
    const usage = rows.nth(i).locator('.row-usage')
    const text = (await usage.innerText().catch(() => '0')).replace('次', '').trim()
    if (Number(text) > 0) {
      await rows.nth(i).locator('.row-name').click()
      clicked = true
      break
    }
  }
  expect(clicked).toBeTruthy()
  // 右侧网格渲染（网格或空态）
  await expect(page.locator('.image-grid').first()).toBeVisible({ timeout: 10_000 })
})

test('标签管理统计卡片渲染', async ({ page }) => {
  await page.goto('/tags')
  await expect(page.getByText('总标签').first()).toBeVisible()
})
