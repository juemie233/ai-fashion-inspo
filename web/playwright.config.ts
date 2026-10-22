import { defineConfig, devices } from '@playwright/test'

/**
 * 前端 E2E 测试配置。
 *
 * - 复用本地 dev server（17777），未启动时自动拉起（npm run dev）
 * - 需要后端（18888）在本地运行；E2E 用例均为读操作断言，
 *   不依赖 API Key（读接口无需认证）
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  // 本地 dev server 单实例：并行时各页面首屏（Vite 按路由懒编译 + 统计接口）
  // 会互相抢，实测偶发超时（同一用例单跑 100% 通过）。11 条用例串行约 1 分钟，
  // 稳定性和可诊断性都更好。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:17777',
    headless: true,
    viewport: { width: 1600, height: 1100 },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:17777',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
