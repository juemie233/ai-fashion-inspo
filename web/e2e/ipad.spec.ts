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
  // 素材/接口偏慢时首屏按钮可能晚于默认 5s 出现（本地并发跑测试时实测偶发）
  await expect(page.getByRole('button', { name: '换一张 →' })).toBeVisible({ timeout: 15000 })
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

/**
 * 触控目标下限（2026-11-16 iPad 验收硬要求）：
 * - 侧栏导航、表格行内动作、行展开等高频操作 ≥44px；
 * - medium 按钮、文本链接、开关等常用控件 ≥36px；
 * - 数字步进视觉尺寸受输入框高度约束，用透明 ::after 扩命中区，这里断言扩展规则存在。
 *
 * 只在 iPad 两个 project（hasTouch + isMobile → pointer: coarse）里执行；
 * 桌面 project 已 testIgnore 本文件。
 */
test('触控目标下限：导航 ≥44、常用按钮/链接 ≥36（命中区包括伪元素扩展）', async ({ page }) => {
  // 本用例要串多个重量级页面（素材库/采集/人物），30s 默认超时在 CI 上不够
  test.setTimeout(90_000)
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)

  // 触屏媒体查询必须命中，否则下面的下限断言没有意义
  expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true)

  // 侧栏导航（含紧凑图标栏）：15 个菜单项全部 ≥44 高
  const navHeights = await page.$$eval('.sidebar .arco-menu-item', (els) =>
    els.map((el) => el.getBoundingClientRect().height),
  )
  expect(navHeights.length).toBeGreaterThan(0)
  for (const h of navHeights) expect(h).toBeGreaterThanOrEqual(44)

  // 常用按钮（arco medium 默认 32px）与文本链接（默认 20~24px）提到 36
  await page.goto('/scraper', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  const mediumHeights = await page.$$eval('.arco-btn-size-medium', (els) =>
    els
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0)
      .map((r) => r.height),
  )
  expect(mediumHeights.length).toBeGreaterThan(0)
  for (const h of mediumHeights) expect(h).toBeGreaterThanOrEqual(36)

  const linkHeights = await page.$$eval('.arco-link', (els) =>
    els
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0)
      .map((r) => r.height),
  )
  for (const h of linkHeights) expect(h).toBeGreaterThanOrEqual(36)

  // 开关：≥44×36（采集页 f2 自动增量卡固定有开关）
  const switchBoxes = await page.$$eval('.arco-switch', (els) =>
    els
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0)
      .slice(0, 5)
      .map((r) => ({ w: r.width, h: r.height })),
  )
  expect(switchBoxes.length).toBeGreaterThan(0)
  for (const box of switchBoxes) {
    expect(box.w).toBeGreaterThanOrEqual(44)
    expect(box.h).toBeGreaterThanOrEqual(36)
  }

  // 表格行展开按钮：≥44（persons 表格可展开行时才存在）
  await page.goto('/persons', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  const expandBoxes = await page.$$eval('.arco-table-expand-btn', (els) =>
    els.slice(0, 5).map((el) => {
      const r = el.getBoundingClientRect()
      return { w: r.width, h: r.height }
    }),
  )
  for (const box of expandBoxes) {
    expect(box.w).toBeGreaterThanOrEqual(44)
    expect(box.h).toBeGreaterThanOrEqual(44)
  }

  // 数字步进：视觉尺寸仍小，但透明 ::after 命中区扩展必须存在（≥36×36 的有效区域）
  await page.goto('/scraper', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  const stepHitArea = await page.$$eval('.arco-input-number-step-button', (els) => {
    const el = els[0]
    if (!el) return null
    const s = getComputedStyle(el, '::after')
    return { content: s.content, position: s.position, top: s.top, left: s.left }
  })
  if (stepHitArea) {
    expect(stepHitArea.content).not.toBe('none')
    expect(stepHitArea.position).toBe('absolute')
    // 上按钮向上扩 30px、向下扩 9px（≥36×36 有效命中区）
    expect(parseFloat(stepHitArea.top)).toBeLessThanOrEqual(-26)
    expect(parseFloat(stepHitArea.left)).toBeLessThanOrEqual(-9)
  }

  await expectNoPageHorizontalScroll(page)
})
