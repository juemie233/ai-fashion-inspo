/** 本地出图（/generate）：环境状态、档位清单、参考图分支、生成历史。

 * **不点「一键出图 / 生成」**：真跑一次要占 GPU ~7 秒，并往素材库写入图片
 * （生成结果入库是设计行为），e2e 冒烟不该有这种副作用。这里只验「真实前后端联通
 * 才能确认」的结构：
 * - ComfyUI 环境状态来自后端探测（就绪 / 显存 / Ollama 占用）；
 * - 档位清单来自 `/api/generate/models`，且**标注了每个档位是纯文字还是需参考图**
 *   （选错档位就会 400，标注是前端唯一的提示）；
 * - 生成历史区域渲染完成（表格或空态，不是永久加载态）。
 */

import { expect, test } from '@playwright/test'

test('出图页：ComfyUI 环境状态与档位清单渲染', async ({ page }) => {
  await page.goto('/generate')

  await expect(page.getByRole('heading', { name: '本地出图' })).toBeVisible()

  // 环境探测结果（就绪时带版本与显存；未就绪时是告警文案，两者都算渲染完成）
  await expect(page.getByText(/ComfyUI.*就绪|ComfyUI.*未|无法连接 ComfyUI/).first()).toBeVisible({
    timeout: 15_000,
  })

  // 档位清单：至少一个「纯文字」档位与一个「需参考图」档位（来自后端模型表）
  await expect(page.getByText(/纯文字/).first()).toBeVisible()
  await expect(page.getByText(/需参考图/).first()).toBeVisible()

  await expect(page.getByText('页面渲染出错')).toHaveCount(0)
})

test('出图页：切换档位后出图按钮文案与参数区仍在（表单可用）', async ({ page }) => {
  await page.goto('/generate')

  // 选一个「需参考图」档位（Qwen i2i），此时页面应提示需要参考图/姿势参考
  const i2iOption = page.getByText(/Qwen-Image 2\.1/).first()
  await expect(i2iOption).toBeVisible({ timeout: 15_000 })
  await i2iOption.click()

  // 出图按钮存在（文案随数量变化，不断言具体数字）
  await expect(page.getByRole('button', { name: /一键出图|生成 \d+ 张/ }).first()).toBeVisible()

  // 参考图相关入口（选择姿势 / 参考图）在这一档可用
  await expect(page.getByText(/选择姿势|参考图/).first()).toBeVisible()
})

test('出图页：生成历史默认折叠，展开后才加载并渲染', async ({ page }) => {
  await page.goto('/generate')

  // 默认隐藏：避免进页面就拉一堆历史（也不影响当前批次候选）
  const toggle = page.getByText('展开生成历史').first()
  await expect(toggle).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.arco-table').first()).toHaveCount(0)

  // 展开后：有记录是表格、无记录是空态，两者都算渲染完成
  await toggle.click()
  await expect(page.locator('.arco-table, .arco-empty').first()).toBeVisible({ timeout: 15_000 })
  // 收起入口文案应变为「收起」（表示状态真的翻转了）
  await expect(page.getByText(/收起生成历史/).first()).toBeVisible()
})
