import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@shared': resolve(__dirname, '../shared'),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['src/**/*.test.ts'],
    // 固定测试时区：CI 默认 UTC，「UTC 串被当成本地时间」这类回归在 UTC 机器上
    // 没有区分度（本地 UTC+8 能测出来、CI 却永远绿）。要用时间断言就得钉死时区。
    env: { TZ: 'Asia/Shanghai' },
    // P1-3：前端覆盖率度量。只统计「可直接单测、且回归价值高」的四类目录；
    // 视图目前只有部分挂载用例，全量 include 会被视图拉低并掩盖真实短板。
    coverage: {
      provider: 'v8',
      include: [
        'src/utils/**/*.ts',
        'src/composables/**/*.ts',
        'src/stores/**/*.ts',
        'src/api/**/*.ts',
      ],
      exclude: ['**/*.test.ts', '**/__tests__/**'],
      reporter: ['text-summary', 'json-summary'],
      thresholds: {
        lines: 50,
        functions: 48,
        branches: 50,
        statements: 50,
      },
    },
  },
})
