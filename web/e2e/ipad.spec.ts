/** iPad 竖屏/横屏布局与触控回归（Chromium 模拟，不能替代 Safari 真机）。 */

import { expect, test } from '@playwright/test'

/** 页面主体不应出现整页横向滚动（表格内部横向滚动是允许的） */
async function expectNoPageHorizontalScroll(page: import('@playwright/test').Page) {
  const metrics = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    bodyOverflowX: getComputedStyle(document.body).overflowX,
  }))
  expect(metrics.bodyOverflowX).toBe('hidden')
  expect(metrics.docScrollWidth).toBeLessThanOrEqual(metrics.innerWidth + 1)
}

test('固定外壳：内容区独立滚动，侧栏不跟着滚走', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(1500)

  const before = await page.evaluate(() => {
    const main = document.querySelector('.main-content') as HTMLElement
    const sidebar = document.querySelector('.sidebar') as HTMLElement
    return {
      mainOverflow: getComputedStyle(main).overflowY,
      mainScrollable: main.scrollHeight > main.clientHeight,
      sidebarTop: sidebar.getBoundingClientRect().top,
      shellHeight: document.querySelector('.app-shell')?.getBoundingClientRect().height,
      viewportHeight: window.innerHeight,
    }
  })

  expect(before.mainOverflow).toBe('auto')
  expect(before.shellHeight).toBeCloseTo(before.viewportHeight, 0)
  expect(before.sidebarTop).toBe(0)
  await expectNoPageHorizontalScroll(page)

  if (before.mainScrollable) {
    await page.locator('.main-content').evaluate((el) => el.scrollTo({ top: 600 }))
    await page.waitForTimeout(200)
    expect(await page.locator('.main-content').evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
    expect(await page.locator('.sidebar').evaluate((el) => el.getBoundingClientRect().top)).toBe(0)
  }
})

test('弹层宽度不超过 iPad 视口，且打开时锁住背景滚动', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(1200)

  await page.getByRole('button', { name: '保存为合集' }).click()
  await page.waitForTimeout(300)

  const modal = page.locator('.arco-modal:visible').first()
  await expect(modal).toBeVisible()
  const box = await modal.boundingBox()
  const viewport = page.viewportSize()!
  expect(box!.width).toBeLessThanOrEqual(viewport.width - 31)
  await expect
    .poll(() => page.locator('.main-content').evaluate((el) => getComputedStyle(el).overflowY))
    .toBe('hidden')

  await modal.getByRole('button', { name: '取消' }).click()
  await expect
    .poll(() => page.locator('.main-content').evaluate((el) => getComputedStyle(el).overflowY))
    .toBe('auto')
})

test('随机评分页在 iPad 上可打开并显示评分入口', async ({ page }) => {
  await page.goto('/random-rating')
  await page.waitForTimeout(1800)

  await expect(page.getByRole('heading', { name: '随机评分' })).toBeVisible()
  await expect(page.getByRole('button', { name: '换一张 →' })).toBeVisible()
  // 有图片时星级可点；没有图片时至少空态可见
  const stars = page.locator('.arco-rate-character')
  const empty = page.locator('.arco-empty')
  expect((await stars.count()) > 0 || (await empty.count()) > 0).toBeTruthy()
  await expectNoPageHorizontalScroll(page)
})

test('收藏合集在 iPad 上列表不再固定 260px 挤占内容', async ({ page }) => {
  await page.goto('/collections')
  await page.waitForTimeout(1200)

  const list = page.locator('.collection-list')
  if (await list.count()) {
    const width = (await list.boundingBox())!.width
    const viewport = page.viewportSize()!
    if (viewport.width <= 900) {
      // 窄屏应占满可用宽度（不再固定 260）
      expect(width).toBeGreaterThan(viewport.width * 0.5)
    } else {
      // 横屏仍保留左侧 260px 列表（内容区还够宽）
      expect(width).toBeLessThanOrEqual(300)
    }
  }
  await expectNoPageHorizontalScroll(page)
})
