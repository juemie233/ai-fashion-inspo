import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://localhost:18888'
  const frontendPort = parseInt(env.VITE_FRONTEND_PORT || '17777', 10)

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    server: {
      port: frontendPort,
      watch: {
        // 忽略编辑器/工具原子写入的临时目录（形如 `.文件名.PID.uuid.tmpdir`），
        // 避免 Vite 监听这些瞬时文件时触发 EBUSY（resource busy or locked）崩溃
        ignored: ['**/.*.tmpdir', '**/.*.tmpdir/**'],
      },
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: backendUrl.replace(/^http/, 'ws'),
          ws: true,
        },
      },
    },
  }
})
