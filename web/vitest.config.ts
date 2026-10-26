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
  },
})
