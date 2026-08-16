import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { execSync } from 'child_process'

/**
 * 从后端代码计算 schema 版本，注入前端全局常量 __SCHEMA_VERSION__。
 *
 * 前端不再硬编码期望版本：每次 dev 启动 / build 时调用后端
 * compute_schema_version()，自动与后端代码对齐，消除「后端改了
 * db_migrations / API_CONTRACT_VERSION 但前端常量漏同步」的问题。
 * 计算失败（如 python 不可用）时返回空串，前端将跳过版本校验。
 */
function resolveSchemaVersion(): string {
  try {
    const out = execSync('python scripts/compute_schema_version.py', {
      cwd: resolve(__dirname, '..'),
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    return out.trim()
  } catch (e) {
    console.warn('[vite] 无法计算 schema_version，前端将跳过版本校验：', e)
    return ''
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://localhost:18888'
  const frontendPort = parseInt(env.VITE_FRONTEND_PORT || '17777', 10)
  const schemaVersion = resolveSchemaVersion()

  return {
    plugins: [vue()],
    define: {
      __SCHEMA_VERSION__: JSON.stringify(schemaVersion),
    },
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
        '@shared': resolve(__dirname, '../shared'),
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
