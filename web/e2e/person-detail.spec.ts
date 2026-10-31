/** 人物模块（/persons 列表 + /persons/:id 详情）：双 Tab 与「进详情再返回」上下文保持。

 * 单测覆盖了 composable 的 URL 同步逻辑，但「URL 真的被路由带过去、返回后又带回来」
 * 只有真实浏览器能验——这正是用户最容易感知的「点进去再返回，筛选全没了」。
 */

import { expect, test } from '@playwright/test'

test('人物列表：双 Tab 切换写入 URL', async ({ page }) => {
  await page.goto('/persons?kind=blogger')

  await expect(page.getByRole('heading', { name: '人物管理' })).toBeVisible()
  await expect(page.locator('.person-name').first()).toBeVisible({ timeout: 15_000 })

  // 切到职业模特：URL 的 kind 必须跟着变（刷新/分享后仍停在该 Tab）
  // 注意要点 Arco 的 tab 容器，点文字节点不一定触发切换
  await page.locator('.arco-tabs-tab', { hasText: '职业模特' }).first().click()
  await expect(page).toHaveURL(/kind=model/)
  await expect(page.locator('.arco-tabs-tab-active').first()).toHaveText('职业模特')

  await page.locator('.arco-tabs-tab', { hasText: '穿搭博主' }).first().click()
  await expect(page).toHaveURL(/kind=blogger/)
})

test('人物列表 → 详情 → 返回：筛选与排序上下文保持', async ({ page }) => {
  await page.goto('/persons?kind=blogger&sort=name')

  const firstName = page.locator('.person-name').first()
  await expect(firstName).toBeVisible({ timeout: 15_000 })
  const name = (await firstName.innerText()).trim()
  // 详情入口是行内「详情」按钮（点名字/行本身不跳转）
  await page.locator('.arco-table-tr').nth(1).getByRole('button', { name: '详情' }).first().click()

  // 详情页：URL 是 /persons/<数字>，页面出现该人物名
  await expect(page).toHaveURL(/\/persons\/\d+/)
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('页面渲染出错')).toHaveCount(0)

  // 浏览器返回：列表上下文（kind + sort）应仍在 URL 上
  await page.goBack()
  await expect(page).toHaveURL(/\/persons\b/)
  await expect(page).toHaveURL(/kind=blogger/)
  await expect(page).toHaveURL(/sort=name/)
  await expect(page.locator('.person-name').first()).toBeVisible({ timeout: 15_000 })
})

test('人物详情页：统计与人脸区域渲染，不出现渲染错误', async ({ page }) => {
  await page.goto('/persons?kind=blogger')

  await expect(page.locator('.person-name').first()).toBeVisible({ timeout: 15_000 })
  await page.locator('.arco-table-tr').nth(1).getByRole('button', { name: '详情' }).first().click()
  await expect(page).toHaveURL(/\/persons\/\d+/)

  // 详情页关键区块（素材统计 / 人脸 / 风格画像 至少渲染出一个）
  await expect(page.getByText(/素材|人脸|风格画像/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('页面渲染出错')).toHaveCount(0)
})
